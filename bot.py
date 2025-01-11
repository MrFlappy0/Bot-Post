import praw
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
import dropbox
import json
import os
from threading import Thread
import asyncio
import logging
from datetime import datetime

# Configuration des logs
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Environnement (Remplacez par vos variables Railways)
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
REDDIT_SECRET = os.getenv("REDDIT_SECRET")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DROPBOX_ACCESS_TOKEN = os.getenv("DROPBOX_ACCESS_TOKEN")
ADMIN_TELEGRAM_ID = os.getenv("ADMIN_TELEGRAM_ID")  # ID de l'administrateur Telegram

# Initialisation de Dropbox
dropbox_client = dropbox.Dropbox(DROPBOX_ACCESS_TOKEN)

# Fonction pour lire config.json depuis Dropbox
def load_config_from_dropbox():
    try:
        logger.info("Tentative de téléchargement de config.json depuis Dropbox.")
        _, res = dropbox_client.files_download("/config.json")
        config = json.loads(res.content)
        logger.info("Configuration chargée avec succès depuis Dropbox.")
        return config
    except Exception as e:
        logger.error(f"Erreur lors du chargement de config.json : {e}")
        send_admin_alert(f"Erreur critique : Impossible de charger config.json.\n\n{e}")
        return None

# Charger la configuration depuis Dropbox
config = load_config_from_dropbox()
if not config:
    logger.critical("Impossible de charger config.json depuis Dropbox. Arrêt du script.")
    raise ValueError("Impossible de charger config.json depuis Dropbox.")

SUBREDDITS = config["subreddits"]
TELEGRAM_CHAT_ID = config["telegram_chat_id"]

# Initialisation de Reddit et Telegram
reddit = praw.Reddit(
    client_id=REDDIT_CLIENT_ID,
    client_secret=REDDIT_SECRET,
    user_agent=REDDIT_USER_AGENT,
)

telegram_bot = Bot(token=TELEGRAM_TOKEN)

# Chemins de sauvegarde JSON
LOGS_FILE = "/logs.json"
POSTS_FILE = "/posts.json"

# Initialisation des fichiers sur Dropbox
def init_dropbox_file(path):
    try:
        dropbox_client.files_get_metadata(path)
        logger.info(f"Fichier {path} trouvé dans Dropbox.")
    except dropbox.exceptions.ApiError:
        dropbox_client.files_upload(json.dumps([]).encode(), path)
        logger.info(f"Fichier {path} créé dans Dropbox.")

init_dropbox_file(LOGS_FILE)
init_dropbox_file(POSTS_FILE)

# Lecture/Écriture sur Dropbox
def read_from_dropbox(path):
    try:
        _, res = dropbox_client.files_download(path)
        logger.info(f"Lecture réussie depuis {path} dans Dropbox.")
        return json.loads(res.content)
    except Exception as e:
        logger.error(f"Erreur lors de la lecture de {path} dans Dropbox : {e}")
        return []

def write_to_dropbox(path, data):
    try:
        dropbox_client.files_upload(json.dumps(data).encode(), path, mode=dropbox.files.WriteMode.overwrite)
        logger.info(f"Écriture réussie dans {path} sur Dropbox.")
    except Exception as e:
        logger.error(f"Erreur lors de l'écriture dans {path} sur Dropbox : {e}")

# Fonction pour envoyer une alerte à l'administrateur Telegram
def send_admin_alert(message):
    try:
        telegram_bot.send_message(chat_id=ADMIN_TELEGRAM_ID, text=f"🚨 ALERTE CRITIQUE 🚨\n\n{message}")
        logger.info("Alerte envoyée à l'administrateur.")
    except Exception as e:
        logger.error(f"Erreur lors de l'envoi de l'alerte à l'administrateur : {e}")

# Archivage quotidien des logs
def archive_logs():
    try:
        date_str = datetime.now().strftime("%Y-%m-%d")
        archive_path = f"/logs/{date_str}_logs.json"
        _, res = dropbox_client.files_download(LOGS_FILE)
        dropbox_client.files_upload(res.content, archive_path, mode=dropbox.files.WriteMode.overwrite)
        logger.info(f"Logs archivés pour la date {date_str}.")
    except Exception as e:
        logger.error(f"Erreur lors de l'archivage des logs : {e}")
        send_admin_alert(f"Erreur critique : Impossible d'archiver les logs.\n\n{e}")

# Détection et envoi des médias
async def send_media(chat_id, submission):
    try:
        if hasattr(submission, "post_hint"):
            if submission.post_hint == "image":
                await telegram_bot.send_photo(chat_id=chat_id, photo=submission.url, caption=submission.title)
                logger.info(f"Image envoyée : {submission.id} - {submission.url}")
            elif submission.post_hint == "hosted:video" and hasattr(submission, "media"):
                video_url = submission.media["reddit_video"]["fallback_url"]
                await telegram_bot.send_video(chat_id=chat_id, video=video_url, caption=submission.title)
                logger.info(f"Vidéo envoyée : {submission.id} - {video_url}")
            elif submission.post_hint in ["rich:video", "link"] and ".gif" in submission.url:
                await telegram_bot.send_animation(chat_id=chat_id, animation=submission.url, caption=submission.title)
                logger.info(f"GIF envoyé : {submission.id} - {submission.url}")
    except Exception as e:
        logger.error(f"Erreur lors de l'envoi de médias pour {submission.id} : {e}")
        send_admin_alert(f"Erreur critique : Échec de l'envoi de médias pour le post {submission.id}.\n\n{e}")

# Fonction principale pour surveiller Reddit
def monitor_reddit():
    logger.info("Démarrage de la surveillance des subreddits.")
    posted_ids = set(read_from_dropbox(POSTS_FILE))

    while True:
        for subreddit in SUBREDDITS:
            try:
                logger.debug(f"Vérification des nouveaux posts sur le subreddit : {subreddit}")
                for submission in reddit.subreddit(subreddit).new(limit=10):
                    if submission.id not in posted_ids:
                        if hasattr(submission, "post_hint") and submission.post_hint in ["image", "hosted:video", "rich:video", "link"]:
                            asyncio.run(send_media(TELEGRAM_CHAT_ID, submission))
                        posted_ids.add(submission.id)
                        write_to_dropbox(POSTS_FILE, list(posted_ids))
            except Exception as e:
                logger.error(f"Erreur lors de la surveillance de {subreddit} : {e}")
                send_admin_alert(f"Erreur critique : Problème lors de la surveillance de {subreddit}.\n\n{e}")

# Commande /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Commande /start appelée.")
    await update.message.reply_text(
        "🤖 Bienvenue dans le bot Reddit Telegram !\n\n"
        "Ce bot surveille les subreddits suivants et publie automatiquement les nouveaux posts contenant des images, vidéos et GIFs :\n\n"
        f"{', '.join(SUBREDDITS)}\n\n"
        "Les posts sont publiés directement dans ce groupe ou cette conversation. Profitez-en !"
    )

# Initialisation du bot Telegram avec Application
def main():
    # Créer une instance de l'application
    logger.info("Initialisation de l'application Telegram.")
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Ajouter les gestionnaires de commandes
    application.add_handler(CommandHandler("start", start))

    # Lancer la surveillance Reddit dans un thread séparé
    logger.info("Démarrage de la surveillance Reddit.")
    Thread(target=monitor_reddit, daemon=True).start()

    # Planifier l'archivage des logs quotidiennement
    archive_logs()

    # Démarrer l'application
    application.run_polling()

if __name__ == "__main__":
    logger.info("Lancement du bot.")
    main()