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
application = ApplicationBuilder().token(TELEGRAM_TOKEN).connection_pool_size(100).request_timeout(60).build()
telegram_bot = application.bot

# Envoi des médias avec retries
@retry(stop=stop_after_attempt(3), wait=wait_fixed(5))
async def send_media_with_retry(chat_id, submission):
    if hasattr(submission, "post_hint"):
        if submission.post_hint == "image":
            await telegram_bot.send_photo(chat_id=chat_id, photo=submission.url)
            logger.info(f"Image envoyée : {submission.id} - {submission.url}")
        elif submission.post_hint == "hosted:video" and hasattr(submission, "media"):
            video_url = submission.media["reddit_video"]["fallback_url"]
            await telegram_bot.send_video(chat_id=chat_id, video=video_url)
            logger.info(f"Vidéo envoyée : {submission.id} - {video_url}")
        elif submission.post_hint in ["rich:video", "link"] and ".gif" in submission.url:
            await telegram_bot.send_animation(chat_id=chat_id, animation=submission.url)
            logger.info(f"GIF envoyé : {submission.id} - {submission.url}")

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

# Fonction principale pour surveiller Reddit
def monitor_reddit():
    logger.info("Démarrage de la surveillance des subreddits.")
    posted_ids = set()

    while True:
        try:
            for subreddit in SUBREDDITS:
                logger.debug(f"Vérification des nouveaux posts sur le subreddit : {subreddit}")
                for submission in reddit.subreddit(subreddit).new(limit=10):
                    if submission.id not in posted_ids:
                        if hasattr(submission, "post_hint") and submission.post_hint in ["image", "hosted:video", "rich:video", "link"]:
                            asyncio.run(send_media(TELEGRAM_CHAT_ID, submission))
                        posted_ids.add(submission.id)
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

def is_admin(user_id):
    return str(user_id) == str(ADMIN_TELEGRAM_ID)

# Commandes pour gérer les subreddits
async def add_subreddit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Vous n'êtes pas autorisé à effectuer cette commande.")
        return
    if not context.args:
        await update.message.reply_text("Veuillez fournir un subreddit à ajouter.")
        return
    subreddit = context.args[0]
    if subreddit not in SUBREDDITS:
        SUBREDDITS.append(subreddit)
        update_config_to_local()
        await update.message.reply_text(f"✅ Le subreddit `{subreddit}` a été ajouté avec succès !")
    else:
        await update.message.reply_text(f"Le subreddit `{subreddit}` est déjà surveillé.")

async def remove_subreddit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Vous n'êtes pas autorisé à effectuer cette commande.")
        return
    if not context.args:
        await update.message.reply_text("Veuillez fournir un subreddit à supprimer.")
        return
    subreddit = context.args[0]
    if subreddit in SUBREDDITS:
        SUBREDDITS.remove(subreddit)
        update_config_to_local()
        await update.message.reply_text(f"✅ Le subreddit `{subreddit}` a été supprimé avec succès !")
    else:
        await update.message.reply_text(f"Le subreddit `{subreddit}` n'est pas surveillé.")

async def list_subreddits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Vous n'êtes pas autorisé à effectuer cette commande.")
        return
    await update.message.reply_text(
        f"📜 Liste des subreddits surveillés :\n\n{', '.join(SUBREDDITS)}"
    )

# Fonction pour mettre à jour config.json localement
def update_config_to_local():
    config_path = "./config.json"
    try:
        config_data = {
            "subreddits": SUBREDDITS,
            "telegram_chat_id": TELEGRAM_CHAT_ID,
            "admin_id": ADMIN_TELEGRAM_ID,
        }
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=4)
        logger.info("Configuration mise à jour avec succès sur le serveur.")
    except Exception as e:
        logger.error(f"Erreur lors de la mise à jour de config.json : {e}")
        send_admin_alert(f"Erreur critique : Impossible de mettre à jour config.json.\n\n{e}")

# Fonction principale
def main():
    logger.info("Initialisation de l'application Telegram.")
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("addsub", add_subreddit))
    application.add_handler(CommandHandler("removesub", remove_subreddit))
    application.add_handler(CommandHandler("list", list_subreddits))

    # Lancer la

    logger.info("Démarrage de la surveillance Reddit.")
    Thread(target=monitor_reddit, daemon=True).start()

    # Démarrer l'application Telegram
    application.run_polling()

if __name__ == "__main__":
    logger.info("Lancement du bot.")
    main()