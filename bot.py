import asyncio
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
from telegram.ext import Application, CommandHandler, MessageHandler, filters

TEMP_DIR = "temp_files"
os.makedirs(TEMP_DIR, exist_ok=True)

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
ADMIN_CHAT_ID = os.getenv("1073675668")  # ID Telegram de l'administrateur

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
        # Tente de charger le fichier depuis Dropbox
        _, res = dropbox_client.files_download(DROPBOX_FILE_PATH_SUBREDDITS)
        current_subreddits = json.loads(res.content.decode("utf-8"))

        if not current_subreddits:  # Si le fichier est vide
            raise ValueError("Le fichier des subreddits est vide.")

        logging.info("Les subreddits suivis ont été chargés depuis Dropbox.")
        return current_subreddits

    except (dropbox.exceptions.ApiError, ValueError, json.JSONDecodeError):
        # Si le fichier n'existe pas ou est vide, on initialise avec les subreddits par défaut
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


def escape_markdown(text):
    """Échappe les caractères spéciaux Markdown."""
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{char}" if char in escape_chars else char for char in text)

def fetch_and_send_new_posts():
    """
    Récupère les nouveaux posts des subreddits configurés, traite les médias et les envoie aux abonnés.
    Journalise chaque étape du processus.
    """
    if not subreddits:
        logging.error("❌ Aucun subreddit configuré. Vérifiez le fichier /subreddits.json.")
        return

    try:
        reddit.user.me()
        logging.info("✅ Connexion Reddit validée.")
    except Exception as e:
        logging.error(f"❌ Connexion Reddit échouée : {e}")
        return

    for subreddit_name in subreddits:
        try:
            logging.info(f"🔍 Début de la récupération des posts pour : {subreddit_name}")
            subreddit = reddit.subreddit(subreddit_name)

            # Récupérer les posts
            posts = list(subreddit.new(limit=100))
            if not posts:
                logging.warning(f"⚠️ Aucun post trouvé pour : {subreddit_name}")
                continue
            logging.info(f"✅ {len(posts)} posts récupérés de : {subreddit_name}")

            # Identifier les posts valides
            valid_posts = {
                submission.id: "".join(
                    c if c.isalnum() or c in (" ", "-", "_") else "_" for c in submission.title
                ) + "." + submission.url.split(".")[-1]
                for submission in posts if submission.id not in sent_posts and is_media_post(submission)
            }
            if not valid_posts:
                logging.info(f"⚠️ Aucun média valide trouvé dans les posts de : {subreddit_name}")
                continue

            logging.info(f"🎞️ {len(valid_posts)} médias valides détectés.")

            # Télécharger les médias
            downloads = download_media_parallel(valid_posts)
            logging.info("📥 Téléchargement terminé.")

            # Traiter les téléchargements
            for submission in posts:
                if submission.id in downloads and downloads[submission.id]:
                    filepath = downloads[submission.id]
                    if os.path.getsize(filepath) > 50 * 1024 * 1024:  # Si fichier > 50MB, compresser
                        filepath = compress_file(filepath)

                    media_type = "image" if filepath.endswith(('.jpg', '.jpeg', '.png', '.gif')) else "video"
                    for chat_id in subscribers.keys():
                        send_media_to_telegram(chat_id, filepath, media_type)

                    # Mettre à jour les statistiques
                    update_temporal_stats(submission, media_type)

                    # Supprimer le fichier temporaire
                    delete_file(filepath)

                    # Marquer comme envoyé
                    sent_posts.add(submission.id)
                    logging.info(f"✅ Post {submission.id} envoyé avec succès.")

            # Sauvegarder les données après traitement
            save_data()
            logging.info(f"📝 Données sauvegardées après traitement des posts pour : {subreddit_name}")

        except Exception as e:
            logging.error(f"❌ Erreur lors de la récupération ou du traitement des posts pour : {subreddit_name} - {e}")


def is_media_post(submission):
    """
    Vérifie si le post contient un média supporté (image, vidéo, GIF) et journalise les résultats.
    """
    try:
        valid_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.mp4', '.webm')
        is_gallery = hasattr(submission, 'is_gallery') and submission.is_gallery
        is_valid = (
            submission.url.endswith(valid_extensions) or
            submission.url.startswith("https://v.redd.it") or
            is_gallery
        )
        if is_valid:
            logging.info(f"📸 Post {submission.id} contient un média supporté : {submission.url}")
        else:
            logging.debug(f"🚫 Post {submission.id} ne contient pas de média valide : {submission.url}")
        return is_valid
    except Exception as e:
        logging.error(f"❌ Erreur lors de la vérification du média pour le post {submission.id} : {e}")
        return False
    

def download_media_parallel(posts):
    """
    Télécharge les médias de plusieurs posts en parallèle et journalise le progrès.
    """
    results = {}
    logging.info(f"📥 Début du téléchargement parallèle pour {len(posts)} médias.")

    def download(url, filename):
        try:
            filepath = os.path.join(TEMP_DIR, filename)
            response = requests.get(url, stream=True)
            response.raise_for_status()
            with open(filepath, "wb") as file:
                for chunk in response.iter_content(chunk_size=8192):
                    file.write(chunk)
            results[url] = filepath
            logging.info(f"✅ Téléchargement réussi : {url} -> {filepath}")
        except Exception as e:
            logging.error(f"❌ Erreur lors du téléchargement du média {url} : {e}")
            results[url] = None

    with ThreadPoolExecutor(max_workers=10) as executor:
        for post_id, filename in posts.items():
            executor.submit(download, reddit.submission(post_id).url, filename)

    logging.info(f"📦 Téléchargement parallèle terminé.")
    return results


def update_temporal_stats(submission, media_type):
    """
    Met à jour les statistiques temporelles pour un post et journalise l'opération.
    """
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        stats.setdefault("temporal", {}).setdefault(submission.subreddit.display_name, []).append({
            "time": now,
            "type": media_type,
            "title": submission.title
        })
        save_file_to_dropbox(DROPBOX_FILE_PATH_STATS, stats)
        logging.info(f"📊 Statistiques mises à jour pour le post {submission.id}.")
    except Exception as e:
        logging.error(f"❌ Erreur lors de la mise à jour des statistiques pour le post {submission.id} : {e}")


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
    Envoie une notification à l'administrateur Telegram et journalise les résultats.
    """
    logging.info(f"Tentative d'envoi de la notification à l'administrateur : {message}")
    try:
        bot.send_message(chat_id=ADMIN_CHAT_ID, text=message)
        logging.info("Notification envoyée avec succès à l'administrateur.")
    except Exception as e:
        logging.error(f"Erreur lors de l'envoi de la notification à l'administrateur : {e}")

def validate_config():
    """
    Valide la configuration initiale : subreddits et connexion Reddit.
    """
    if not subreddits:
        logging.error("❌ Aucun subreddit configuré. Vérifiez le fichier /subreddits.json.")
        return False

    try:
        reddit.user.me()
        logging.info("✅ Connexion Reddit réussie.")
        return True
    except Exception as e:
        logging.error(f"❌ Impossible de se connecter à Reddit : {e}")
        return False

# Exemple d'utilisation dans main()
if __name__ == "__main__":
    load_data()
    if not validate_config():
        exit(1)  # Quitter si la validation échoue

def compress_file(filepath):
    """
    Compresse un fichier volumineux au format Gzip et journalise les résultats.
    """
    compressed_filepath = filepath + ".gz"
    try:
        logging.info(f"Compression du fichier : {filepath}")
        with open(filepath, "rb") as f_in, gzip.open(compressed_filepath, "wb") as f_out:
            f_out.writelines(f_in)
        logging.info(f"Fichier compressé avec succès : {compressed_filepath}")
        return compressed_filepath
    except Exception as e:
        logging.error(f"Erreur lors de la compression du fichier {filepath} : {e}")
        return filepath


def send_media_to_telegram(chat_id, filepath, media_type):
    """
    Envoie une image ou une vidéo à un utilisateur Telegram et journalise les résultats.
    """
    try:
        logging.info(f"Tentative d'envoi du média {filepath} ({media_type}) au chat {chat_id}")
        if media_type == "image":
            with open(filepath, "rb") as file:
                bot.send_photo(chat_id=chat_id, photo=file)
        elif media_type == "video":
            with open(filepath, "rb") as file:
                bot.send_video(chat_id=chat_id, video=file)
        logging.info(f"Média envoyé avec succès au chat {chat_id} : {filepath}")
    except Exception as e:
        logging.error(f"Erreur lors de l'envoi du média {filepath} à {chat_id} : {e}")
        failed_queue.append({"chat_id": chat_id, "filepath": filepath, "media_type": media_type})
        stats["failed"] = len(failed_queue)


def delete_file(filepath):
    """
    Supprime un fichier du système et journalise les résultats.
    """
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
            logging.info(f"Fichier supprimé avec succès : {filepath}")
        else:
            logging.warning(f"Fichier introuvable pour suppression : {filepath}")
    except Exception as e:
        logging.error(f"Erreur lors de la suppression du fichier {filepath} : {e}")


def daily_report():
    """
    Génère un rapport quotidien des statistiques et l'envoie à l'administrateur, avec journalisation détaillée.
    """
    logging.info("Génération du rapport quotidien.")
    today = datetime.now().strftime("%Y-%m-%d")
    report_message = f"📊 Rapport quotidien ({today}):\n"
    report_message += f"Total médias envoyés : {stats['total']}\n"
    report_message += f"Images : {stats['images']}\n"
    report_message += f"Vidéos : {stats['videos']}\n"
    report_message += f"GIFs : {stats['gifs']}\n"
    report_message += f"Envois échoués : {stats['failed']}\n"

    for subreddit, count in stats.get("subreddits", {}).items():
        report_message += f"r/{subreddit} : {count} posts envoyés\n"

    try:
        notify_admin(report_message)
        logging.info("Rapport quotidien envoyé avec succès à l'administrateur.")
    except Exception as e:
        logging.error(f"Erreur lors de l'envoi du rapport quotidien : {e}")


def reload_data():
    """
    Recharge les données depuis Dropbox et journalise les résultats.
    """
    global subscribers, subreddits
    logging.info("Tentative de rechargement des données depuis Dropbox.")
    try:
        subscribers = load_file_from_dropbox(DROPBOX_FILE_PATH_SUBSCRIBERS, {})
        subreddits = initialize_subreddits_in_dropbox()
        logging.info("Données rechargées avec succès depuis Dropbox.")
    except Exception as e:
        logging.error(f"Erreur lors du rechargement des données depuis Dropbox : {e}")


def split_message(message, max_length=4096):
    """
    Divise un message long en plusieurs parties pour Telegram, avec journalisation.
    """
    logging.info("Division d'un message long pour l'envoi à Telegram.")
    parts = [message[i:i + max_length] for i in range(0, len(message), max_length)]
    logging.debug(f"Message divisé en {len(parts)} parties.")
    return parts

async def send_long_message(chat_id, message, context):
    for part in split_message(message):
        await context.bot.send_message(chat_id=chat_id, text=part, parse_mode="Markdown")


async def main_tasks():
    while True:
        try:
            # Recharger les abonnés et subreddits toutes les 5 minutes
            if time.time() - last_reload > 300:
                logging.info("♻️ Rechargement des abonnés et subreddits depuis Dropbox...")
                subscribers = load_file_from_dropbox(DROPBOX_FILE_PATH_SUBSCRIBERS, {})
                subreddits = load_file_from_dropbox(DROPBOX_FILE_PATH_SUBREDDITS, [])
                last_reload = time.time()

            # Récupérer et envoyer les nouveaux posts
            fetch_and_send_new_posts()

            # Réessayer les envois échoués
            if failed_queue:
                logging.info(f"🔁 Tentative de réenvoi pour {len(failed_queue)} fichiers échoués.")
                retry_failed_queue()

            # Nettoyer les fichiers temporaires
            clean_temp_directory()

            # Sauvegarder les données régulièrement
            save_data()

        except Exception as e:
            logging.error(f"⚠️ Erreur critique dans les tâches principales : {e}")
            notify_admin(f"⚠️ Erreur critique dans les tâches principales : {e}")
        await asyncio.sleep(60)

async def start(update, context):
    """Commande /start pour afficher un message d'accueil et enregistrer l'utilisateur."""
    chat_id = update.effective_chat.id
    username = update.effective_user.username or "Utilisateur inconnu"

    # Ajouter l'utilisateur aux abonnés si nécessaire
    if chat_id not in subscribers:
        subscribers[chat_id] = {"username": username, "joined": datetime.now().isoformat()}
        save_data()  # Sauvegarder immédiatement après l'ajout
        logging.info(f"Nouvel abonné ajouté : {username} (ID : {chat_id})")

    message = (
        "👋 **Bienvenue sur le Reddit Media Bot !**\n\n"
        "📌 **Fonctionnalités principales :**\n"
        "- 🔍 Surveille des subreddits pour récupérer des images, vidéos ou GIFs.\n"
        "- 📤 Envoie les médias directement dans cette conversation Telegram.\n"
        "- 📊 Génère des rapports quotidiens sur l'activité du bot.\n\n"
        "📂 **Subreddits Suivis Actuellement :**\n"
        f"{', '.join(subreddits)}\n\n"
        "⚙️ **Commandes disponibles :**\n"
        "➡️ `/help` - Affiche ce message d'aide.\n"
        "➡️ `/stats` - Affiche les statistiques actuelles.\n"
        "➡️ `/reload` - Recharge les données (abonnés, subreddits).\n"
        "\n💡 *Si vous avez des questions, contactez l'administrateur.*"
    )
    escaped_message = escape_markdown(message)
    await context.bot.send_message(chat_id=chat_id, text=escaped_message, parse_mode="Markdown")
async def help_command(update, context):
    """Commande /help pour afficher un message d'aide détaillé."""
    message = (
        "❓ **Aide et Informations sur le Bot**\n\n"
        "🔧 **Commandes disponibles :**\n"
        "1. `/start` - Affiche le message de bienvenue.\n"
        "2. `/help` - Affiche ce message d'aide.\n"
        "3. `/stats` - Montre les statistiques des médias envoyés.\n"
        "4. `/reload` - Recharge les données des abonnés et des subreddits.\n\n"
        "📋 **Explications :**\n"
        "- Le bot surveille automatiquement les subreddits configurés pour récupérer des images, vidéos et GIFs.\n"
        "- Ces médias sont envoyés ici dès qu'ils sont disponibles.\n\n"
        "💬 *Pour toute question, contactez l'administrateur.*"
    )
    escaped_message = escape_markdown(message)
    await context.bot.send_message(chat_id=update.effective_chat.id, text=escaped_message, parse_mode="Markdown")
async def clean_temp_command(update, context):
    """
    Commande pour nettoyer manuellement le répertoire temporaire.
    """
    clean_temp_directory()  # Appel de la fonction synchrone pour nettoyer
    await context.bot.send_message(chat_id=update.effective_chat.id, text="🧹 Répertoire temporaire nettoyé.")
    logging.info("Commande de nettoyage du répertoire temporaire exécutée.")

async def reload_command(update, context):
    """
    Recharge les abonnés et les subreddits depuis Dropbox.
    """
    reload_data()
    await context.bot.send_message(chat_id=update.effective_chat.id, text="🔄 Données rechargées avec succès.")
    logging.info("Les données ont été rechargées.")
async def stats_command(update, context):
    """
    Affiche les statistiques actuelles des médias envoyés.
    """
    stats_message = (
        "📊 **Statistiques Actuelles**\n\n"
        f"📸 Images envoyées : {stats['images']}\n"
        f"🎥 Vidéos envoyées : {stats['videos']}\n"
        f"🎞️ GIFs envoyés : {stats['gifs']}\n"
        f"📬 Total de médias envoyés : {stats['total']}\n\n"
        "🛠️ *Subreddits actuellement suivis :*\n"
        f"{', '.join(subreddits)}"
    )
    await context.bot.send_message(chat_id=update.effective_chat.id, text=stats_message, parse_mode="Markdown")

async def echo(update, context):
    await update.message.reply_text(f"Commande reçue : {update.message.text}")
async def fallback(update, context):
    await update.message.reply_text("Commande inconnue ou non prise en charge.")
async def error_handler(update, context):
    logging.error(f"Une erreur s'est produite : {context.error}")
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"⚠️ Une erreur s'est produite : {context.error}")

async def get_chat_id(update, context):
    """Renvoie l'chat_id de l'utilisateur actuel."""
    chat_id = update.effective_chat.id
    await update.message.reply_text(f"Votre chat_id est : {chat_id}")
    logging.info(f"Chat ID obtenu : {chat_id}")
async def error_handler(update, context):
    """Gère les erreurs et notifie l'administrateur."""
    logging.error(f"Une erreur s'est produite : {context.error}")
    if ADMIN_CHAT_ID:
        try:
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"⚠️ Une erreur s'est produite : {context.error}")
            logging.info("Erreur notifiée à l'administrateur.")
        except Exception as e:
            logging.error(f"Impossible d'envoyer la notification à l'administrateur : {e}")
    else:
        logging.error("ADMIN_CHAT_ID est vide. Impossible de notifier l'administrateur.")
async def notify_admin(message):
    """Envoie une notification à l'administrateur."""
    logging.info(f"Tentative d'envoi de notification : {message}")
    if not ADMIN_CHAT_ID:
        logging.error("ADMIN_CHAT_ID est vide. Impossible d'envoyer une notification.")
        return
    try:
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=message)
        logging.info("Notification envoyée avec succès.")
    except Exception as e:
        logging.error(f"Erreur lors de l'envoi de la notification : {e}")


if __name__ == "__main__":
    try:
        # Charger les données initiales
        logging.info("🔄 Initialisation : Chargement des données...")
        load_data()

        # Validation de la configuration
        if not validate_config():
            logging.error("❌ Configuration invalide. Arrêt du bot.")
            exit(1)

        # Créer l'application Telegram
        logging.info("🚀 Initialisation de l'application Telegram...")
        application = Application.builder().token(TELEGRAM_TOKEN).build()

        # Ajouter les commandes utilisateur et administrateur
        logging.info("⚙️ Ajout des commandes Telegram...")
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("stats", stats_command))
        application.add_handler(CommandHandler("reload", reload_command))
        application.add_handler(CommandHandler("clean_temp", clean_temp_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback))
        application.add_error_handler(error_handler)

        # Démarrer le rapport quotidien dans un thread séparé
        logging.info("🗓️ Démarrage de la planification des rapports quotidiens...")
        Thread(target=schedule_daily_report, daemon=True).start()

        # Démarrer l'écoute des commandes Telegram (dans le thread principal)
        logging.info("💬 Démarrage de l'écoute des commandes Telegram via polling...")
        application.run_polling()

    except Exception as e:
        logging.critical(f"🚨 Erreur fatale lors du démarrage : {e}")
        notify_admin(f"🚨 Erreur critique au démarrage : {e}") 