import praw
from telegram import Bot, Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import json
import os
from threading import Thread
import asyncio
import logging
from datetime import datetime
from tenacity import retry, stop_after_attempt, wait_fixed
import requests

# Configuration des logs
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Environnement (remplacez par vos variables Railways ou définissez les valeurs localement)
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
REDDIT_SECRET = os.getenv("REDDIT_SECRET")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# Fonction pour envoyer une alerte à l'administrateur Telegram
def send_admin_alert(message):
    try:
        telegram_bot.send_message(chat_id=ADMIN_TELEGRAM_ID, text=f"🚨 ALERTE CRITIQUE 🚨\n\n{message}")
        logger.info("Alerte envoyée à l'administrateur.")
    except Exception as e:
        logger.error(f"Erreur lors de l'envoi de l'alerte à l'administrateur : {e}")

# Fonction pour charger config.json localement
def load_config_from_local():
    config_path = "./config.json"  # Chemin local du fichier config.json
    try:
        logger.info("Tentative de chargement de config.json depuis le serveur.")
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        # Validation des clés dans la configuration
        if not all(key in config for key in ["subreddits", "telegram_chat_id", "admin_id"]):
            raise ValueError("Certaines clés manquent dans config.json.")
        
        logger.info("Configuration chargée avec succès depuis le serveur.")
        return config
    except json.JSONDecodeError as e:
        logger.error(f"Erreur JSON dans config.json : {e}")
        send_admin_alert(f"Erreur JSON dans config.json : {e}")
        raise
    except FileNotFoundError as e:
        logger.error(f"Le fichier config.json est introuvable : {e}")
        send_admin_alert(f"Erreur critique : Fichier config.json introuvable.\n\n{e}")
        raise
    except Exception as e:
        logger.error(f"Erreur lors du chargement de config.json : {e}")
        send_admin_alert(f"Erreur critique : Impossible de charger config.json.\n\n{e}")
        raise

# Charger la configuration
config = load_config_from_local()
SUBREDDITS = config["subreddits"]
TELEGRAM_CHAT_ID = config["telegram_chat_id"]
ADMIN_TELEGRAM_ID = config["admin_id"]

# Initialisation de Reddit et Telegram
reddit = praw.Reddit(
    client_id=REDDIT_CLIENT_ID,
    client_secret=REDDIT_SECRET,
    user_agent=REDDIT_USER_AGENT,
)
application = ApplicationBuilder()\
    .token(TELEGRAM_TOKEN)\
    .connection_pool_size(100)\
    .connect_timeout(30)\
    .read_timeout(60)\
    .build()
telegram_bot = application.bot

# Fonction pour télécharger les médias localement
def download_media(url, file_name):
    try:
        logger.info(f"Téléchargement du média depuis {url} vers {file_name}")
        response = requests.get(url, stream=True)
        response.raise_for_status()
        with open(file_name, "wb") as file:
            for chunk in response.iter_content(chunk_size=8192):
                file.write(chunk)
        logger.info(f"Média téléchargé avec succès : {file_name}")
        return file_name
    except Exception as e:
        logger.error(f"Erreur lors du téléchargement du média {url} : {e}")
        return None

# Envoi des médias avec retries
@retry(stop=stop_after_attempt(3), wait=wait_fixed(5))
async def send_media_with_retry(chat_id, submission):
    if hasattr(submission, "post_hint"):
        media_file = None

        # Gestion des images
        if submission.post_hint == "image":
            media_file = download_media(submission.url, f"./temp/{submission.id}.jpg")
            if media_file:
                await telegram_bot.send_photo(chat_id=chat_id, photo=open(media_file, "rb"))

        # Gestion des vidéos
        elif submission.post_hint == "hosted:video" and hasattr(submission, "media"):
            video_url = submission.media["reddit_video"]["fallback_url"]
            media_file = download_media(video_url, f"./temp/{submission.id}.mp4")
            if media_file:
                await telegram_bot.send_video(chat_id=chat_id, video=open(media_file, "rb"))

        # Gestion des GIFs
        elif submission.post_hint in ["rich:video", "link"] and ".gif" in submission.url:
            media_file = download_media(submission.url, f"./temp/{submission.id}.gif")
            if media_file:
                await telegram_bot.send_animation(chat_id=chat_id, animation=open(media_file, "rb"))

        # Suppression du fichier local après l'envoi
        if media_file:
            os.remove(media_file)
            logger.info(f"Fichier temporaire supprimé : {media_file}")

# Gestion des envois avec délai
async def send_media(chat_id, submission):
    try:
        await send_media_with_retry(chat_id, submission)
        await asyncio.sleep(1)  # Délai entre les envois
    except asyncio.TimeoutError as e:
        logger.error(f"Timeout lors de l'envoi du média pour {submission.id} : {e}")
        send_admin_alert(f"Timeout lors de l'envoi du média : {submission.id}.")
    except Exception as e:
        logger.error(f"Erreur critique pour le média {submission.id} : {e}")
        send_admin_alert(f"Erreur critique lors de l'envoi du média : {submission.id}.\n\n{e}")


def load_posted_ids(file_path="posted_ids.json"):
    try:
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                return set(json.load(f))
        else:
            return set()
    except Exception as e:
        logger.error(f"Erreur lors du chargement de posted_ids : {e}")
        return set()

def save_posted_ids(posted_ids, file_path="posted_ids.json"):
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(list(posted_ids), f, indent=4)
        logger.info(f"Liste des posted_ids mise à jour : {file_path}")
    except Exception as e:
        logger.error(f"Erreur lors de la sauvegarde de posted_ids : {e}")

# Fonction principale pour surveiller Reddit
def monitor_reddit():
    logger.info("Démarrage de la surveillance des subreddits.")
    os.makedirs("./temp", exist_ok=True)  # Crée un dossier temporaire s'il n'existe pas
    posted_ids = load_posted_ids()  # Charge les IDs des posts déjà envoyés

    while True:
        try:
            for subreddit in SUBREDDITS:
                logger.info(f"Recherche des nouveaux posts dans le subreddit : {subreddit}")
                for submission in reddit.subreddit(subreddit).new(limit=10):
                    if submission.id not in posted_ids:
                        logger.info(f"Nouveau post trouvé : {submission.id} - {submission.title}")
                        if hasattr(submission, "post_hint") and submission.post_hint in ["image", "hosted:video", "rich:video", "link"]:
                            asyncio.run(send_media(TELEGRAM_CHAT_ID, submission))
                        posted_ids.add(submission.id)
                        save_posted_ids(posted_ids)  # Sauvegarde après chaque envoi
        except Exception as e:
            logger.error(f"Erreur globale lors de la surveillance de Reddit : {e}")
            send_admin_alert(f"Erreur critique lors de la surveillance Reddit :\n\n{e}")

# Commandes Telegram
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Commande /start appelée.")
    await update.message.reply_text(
        "🤖 Bienvenue dans le bot Reddit Telegram !\n\n"
        "Ce bot surveille les subreddits et publie automatiquement les nouveaux médias.\n\n"
        "Les posts sont publiés directement dans ce groupe ou cette conversation. Profitez-en !"
    )

# Fonction principale
def main():
    logger.info("Initialisation de l'application Telegram.")
    application.add_handler(CommandHandler("start", start))

    # Lancer la surveillance Reddit dans un thread séparé
    logger.info("Démarrage de la surveillance Reddit.")
    Thread(target=monitor_reddit, daemon=True).start()

    # Démarrer l'application Telegram
    application.run_polling()

if __name__ == "__main__":
    logger.info("Lancement du bot.")
    main()