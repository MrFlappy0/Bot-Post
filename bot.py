import os
import json
import praw
import dropbox
import logging
import requests
import gzip
from threading import Thread
from concurrent.futures import ThreadPoolExecutor
from telegram import Bot
from telegram.error import NetworkError, TelegramError
from datetime import datetime, timedelta
from tenacity import retry, stop_after_attempt, wait_fixed
import time

# Chargement des variables d'environnement (Railway)
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
REDDIT_SECRET = os.getenv("REDDIT_SECRET")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "RedditTelegramBot")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DROPBOX_ACCESS_TOKEN = os.getenv("DROPBOX_ACCESS_TOKEN")
DROPBOX_FILE_PATH_POSTS = "/sent_posts.txt"
DROPBOX_FILE_PATH_SUBSCRIBERS = "/subscribers.json"
DROPBOX_FILE_PATH_SUBREDDITS = "/subreddits.json"
DROPBOX_FILE_PATH_STATS = "/stats.json"
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")  # ID Telegram de l'administrateur

# Initialiser Reddit avec PRAW
reddit = praw.Reddit(
    client_id=REDDIT_CLIENT_ID,
    client_secret=REDDIT_SECRET,
    user_agent=REDDIT_USER_AGENT
)

# Initialiser Telegram
bot = Bot(token=TELEGRAM_TOKEN)

# Initialiser Dropbox
dropbox_client = dropbox.Dropbox(DROPBOX_ACCESS_TOKEN)

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)

# Variables globales
sent_posts = set()
subscribers = {}
subreddits = []
stats = {"images": 0, "videos": 0, "gifs": 0, "total": 0, "subreddits": {}, "temporal": {}, "failed": 0}
failed_queue = []  # File d'attente pour les envois échoués
TEMP_DIR = "temp_files"  # Répertoire temporaire pour stocker les fichiers téléchargés
os.makedirs(TEMP_DIR, exist_ok=True)  # Assurez-vous que le répertoire existe


def initialize_subreddits_in_dropbox():
    """
    Initialise les subreddits suivis par défaut dans Dropbox si le fichier n'existe pas ou est vide.
    """
    default_subreddits = [
        "Nudes", "FantasticBreasts", "GoneWild", "cumsluts", "PetiteGoneWild", "RealGirls", 
        "nsfw", "Amateur", "pregnantporn", "NSFW_GIF", "scrubsgonewild", "GoneWildPlus", 
        "NaughtyWives", "snapleaks", "pregnantonlyfans", "Nude_Selfie", "Puffies"
    ]

    try:
        # Charger le fichier depuis Dropbox
        _, res = dropbox_client.files_download(DROPBOX_FILE_PATH_SUBREDDITS)
        current_subreddits = json.loads(res.content.decode("utf-8"))
        
        if not current_subreddits:  # Si le fichier est vide
            raise ValueError("Le fichier des subreddits est vide.")
        
        logging.info("Les subreddits suivis ont été chargés depuis Dropbox.")
        return current_subreddits

    except (dropbox.exceptions.ApiError, ValueError, json.JSONDecodeError):
        # Si le fichier n'existe pas ou est vide, on initialise les subreddits par défaut
        logging.warning("Aucun fichier de subreddits trouvé ou fichier vide. Initialisation par défaut.")
        save_file_to_dropbox(DROPBOX_FILE_PATH_SUBREDDITS, default_subreddits)
        return default_subreddits
    
# Chargement et sauvegarde des données
def load_file_from_dropbox(file_path, default_data):
    try:
        _, res = dropbox_client.files_download(file_path)
        data = json.loads(res.content.decode("utf-8"))
        logging.info(f"Fichier {file_path} chargé depuis Dropbox.")
        return data
    except dropbox.exceptions.ApiError:
        logging.warning(f"Fichier {file_path} introuvable, création d'un nouveau.")
        return default_data

def load_data():
    global sent_posts, subscribers, subreddits, stats

    # Chargement des posts déjà envoyés
    sent_posts = set(load_file_from_dropbox(DROPBOX_FILE_PATH_POSTS, []))
    
    # Chargement des abonnés
    subscribers = load_file_from_dropbox(DROPBOX_FILE_PATH_SUBSCRIBERS, {})
    
    # Initialisation ou chargement des subreddits
    subreddits = initialize_subreddits_in_dropbox()
    
    # Chargement des statistiques
    stats = load_file_from_dropbox(DROPBOX_FILE_PATH_STATS, stats)


@retry(stop=stop_after_attempt(3), wait=wait_fixed(5))
def save_file_to_dropbox(file_path, data):
    try:
        content = json.dumps(data, indent=4)
        dropbox_client.files_upload(
            content.encode("utf-8"),
            file_path,
            mode=dropbox.files.WriteMode("overwrite")
        )
        logging.info(f"Fichier {file_path} sauvegardé sur Dropbox.")
    except Exception as e:
        logging.error(f"Erreur lors de la sauvegarde de {file_path} sur Dropbox : {e}")
        raise e


def load_data():
    global sent_posts, subscribers, subreddits, stats
    sent_posts = set(load_file_from_dropbox(DROPBOX_FILE_PATH_POSTS, []))
    subscribers = load_file_from_dropbox(DROPBOX_FILE_PATH_SUBSCRIBERS, {})
    subreddits = load_file_from_dropbox(DROPBOX_FILE_PATH_SUBREDDITS, ["example_subreddit"])
    stats = load_file_from_dropbox(DROPBOX_FILE_PATH_STATS, stats)


def save_data():
    save_file_to_dropbox(DROPBOX_FILE_PATH_POSTS, list(sent_posts))
    save_file_to_dropbox(DROPBOX_FILE_PATH_SUBSCRIBERS, subscribers)
    save_file_to_dropbox(DROPBOX_FILE_PATH_SUBREDDITS, subreddits)
    save_file_to_dropbox(DROPBOX_FILE_PATH_STATS, stats)

# Téléchargement parallèle
def download_media_parallel(posts):
    """
    Télécharge les médias de plusieurs posts en parallèle.
    :param posts: Dictionnaire des posts {id: filename}.
    :return: Dictionnaire {id: filepath ou None en cas d'échec}.
    """
    results = {}

    def download(url, filename):
        try:
            filepath = os.path.join(TEMP_DIR, filename)
            response = requests.get(url, stream=True)
            response.raise_for_status()
            with open(filepath, "wb") as file:
                for chunk in response.iter_content(chunk_size=8192):
                    file.write(chunk)
            results[url] = filepath
        except Exception as e:
            logging.error(f"Erreur lors du téléchargement du média {url} : {e}")
            results[url] = None

    with ThreadPoolExecutor(max_workers=10) as executor:
        for post_id, filename in posts.items():
            executor.submit(download, reddit.submission(post_id).url, filename)

    return results

# Récupération et envoi des posts
def fetch_and_send_new_posts():
    for subreddit_name in subreddits:
        subreddit = reddit.subreddit(subreddit_name)
        logging.info(f"Récupération des posts pour le subreddit : {subreddit_name}")
        posts = list(subreddit.new(limit=100))  # Limite raisonnable par itération

        # Téléchargement parallèle des médias
        downloads = download_media_parallel({
            submission.id: "".join(
                c if c.isalnum() or c in (" ", "-", "_") else "_" for c in submission.title
            ) + "." + submission.url.split(".")[-1]
            for submission in posts if submission.id not in sent_posts and is_media_post(submission)
        })

        # Traiter les téléchargements
        for submission in posts:
            if submission.id in downloads and downloads[submission.id]:
                filepath = downloads[submission.id]
                if os.path.getsize(filepath) > 50 * 1024 * 1024:  # Compression si nécessaire
                    filepath = compress_file(filepath)

                media_type = "image" if filepath.endswith(('.jpg', '.jpeg', '.png', '.gif')) else "video"
                for chat_id in subscribers.keys():
                    send_media_to_telegram(chat_id, filepath, media_type)

                update_temporal_stats(submission, media_type)
                delete_file(filepath)

                sent_posts.add(submission.id)
                save_data()

# Statistiques et Nettoyage
def update_temporal_stats(submission, media_type):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stats.setdefault("temporal", {}).setdefault(submission.subreddit.display_name, []).append({
        "time": now,
        "type": media_type,
        "title": submission.title
    })
    save_file_to_dropbox(DROPBOX_FILE_PATH_STATS, stats)


def retry_failed_queue():
    for task in failed_queue[:]:
        try:
            send_media_to_telegram(task['chat_id'], task['filepath'], task['media_type'])
            failed_queue.remove(task)
        except Exception as e:
            logging.error(f"Erreur lors de la tentative de réenvoi pour {task['filepath']} : {e}")


def clean_temp_directory():
    now = time.time()
    for filename in os.listdir(TEMP_DIR):
        filepath = os.path.join(TEMP_DIR, filename)
        try:
            if os.path.isfile(filepath) and (now - os.path.getmtime(filepath)) > 24 * 3600:  # Plus vieux que 24h
                os.remove(filepath)
                logging.info(f"Fichier temporaire supprimé : {filepath}")
        except Exception as e:
            logging.error(f"Erreur lors de la suppression du fichier temporaire {filepath} : {e}")


def schedule_daily_report():
    """
    Planifie un rapport quotidien envoyé à l'administrateur.
    """
    while True:
        now = datetime.now()
        next_run = (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
        time_to_wait = (next_run - now).total_seconds()
        time.sleep(time_to_wait)
        daily_report()
def notify_admin(message):
    """
    Envoie une notification à l'administrateur Telegram.
    """
    try:
        bot.send_message(chat_id=ADMIN_CHAT_ID, text=message)
    except Exception as e:
        logging.error(f"Erreur lors de la notification à l'administrateur : {e}")
def is_media_post(submission):
    """
    Vérifie si le post contient un média supporté (image, vidéo, GIF).
    """
    valid_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.mp4', '.webm')
    return (
        submission.url.endswith(valid_extensions) or
        submission.url.startswith("https://v.redd.it") or
        submission.is_gallery
    )
def compress_file(filepath):
    """
    Compresse un fichier volumineux au format Gzip.
    """
    compressed_filepath = filepath + ".gz"
    try:
        with open(filepath, "rb") as f_in, gzip.open(compressed_filepath, "wb") as f_out:
            f_out.writelines(f_in)
        logging.info(f"Fichier compressé : {compressed_filepath}")
        return compressed_filepath
    except Exception as e:
        logging.error(f"Erreur lors de la compression du fichier {filepath} : {e}")
        return filepath
def send_media_to_telegram(chat_id, filepath, media_type):
    """
    Envoie une image ou une vidéo à un utilisateur Telegram.
    """
    try:
        if media_type == "image":
            with open(filepath, "rb") as file:
                bot.send_photo(chat_id=chat_id, photo=file)
        elif media_type == "video":
            with open(filepath, "rb") as file:
                bot.send_video(chat_id=chat_id, video=file)
        logging.info(f"Média envoyé à {chat_id} : {filepath}")
    except Exception as e:
        logging.error(f"Erreur lors de l'envoi du média {filepath} à {chat_id} : {e}")
        failed_queue.append({"chat_id": chat_id, "filepath": filepath, "media_type": media_type})
        stats["failed"] = len(failed_queue)
def delete_file(filepath):
    """
    Supprime un fichier du système.
    """
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
            logging.info(f"Fichier temporaire supprimé : {filepath}")
        else:
            logging.warning(f"Fichier non trouvé pour suppression : {filepath}")
    except Exception as e:
        logging.error(f"Erreur lors de la suppression du fichier {filepath} : {e}")
def daily_report():
    """
    Génère un rapport quotidien des statistiques et l'envoie à l'administrateur.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    report_message = f"📊 Rapport quotidien ({today}):\n"
    report_message += f"Total médias envoyés : {stats['total']}\n"
    report_message += f"Images : {stats['images']}\n"
    report_message += f"Vidéos : {stats['videos']}\n"
    report_message += f"GIFs : {stats['gifs']}\n"
    report_message += f"Envois échoués : {stats['failed']}\n"

    for subreddit, count in stats.get("subreddits", {}).items():
        report_message += f"r/{subreddit} : {count} posts envoyés\n"

    notify_admin(report_message)

def reload_data():
    global subscribers, subreddits
    subscribers = load_file_from_dropbox(DROPBOX_FILE_PATH_SUBSCRIBERS, {})
    subreddits = load_file_from_dropbox(DROPBOX_FILE_PATH_SUBREDDITS, ["example_subreddit"])
    logging.info("Données rechargées depuis Dropbox.")



if __name__ == "__main__":
    # Charger les données initiales
    logging.info("Chargement des données...")
    load_data()
    
    # Lancer le rapport quotidien dans un thread séparé
    logging.info("Démarrage de la planification du rapport quotidien.")
    Thread(target=schedule_daily_report, daemon=True).start()

    # Variables pour gestion dynamique
    last_reload = time.time()

    while True:
        try:
            # Recharger les abonnés et subreddits toutes les 5 minutes
            if time.time() - last_reload > 300:  # 300 secondes = 5 minutes
                logging.info("Rechargement des abonnés et subreddits depuis Dropbox.")
                subscribers = load_file_from_dropbox(DROPBOX_FILE_PATH_SUBSCRIBERS, {})
                subreddits = load_file_from_dropbox(DROPBOX_FILE_PATH_SUBREDDITS, ["example_subreddit"])
                last_reload = time.time()

            # Récupérer et envoyer les nouveaux posts
            fetch_and_send_new_posts()

            # Réessayer les envois échoués
            if failed_queue:
                logging.info(f"Tentative de réenvoi pour {len(failed_queue)} fichiers échoués.")
                retry_failed_queue()

            # Nettoyer les fichiers temporaires
            clean_temp_directory()

            # Sauvegarder les données régulièrement
            save_data()

        except KeyboardInterrupt:
            logging.warning("Interruption du bot par l'utilisateur. Arrêt en cours...")
            break
        except Exception as e:
            # Gestion des erreurs critiques
            logging.error(f"Erreur critique dans la boucle principale : {e}")
            notify_admin(f"⚠️ Le bot a rencontré une erreur critique : {e}")

        # Pause entre les itérations pour limiter la charge
        time.sleep(60)