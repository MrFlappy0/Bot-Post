import praw
from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler, CallbackContext
import dropbox
import json
import os
from threading import Thread
import asyncio

# Environnement (Remplacez par vos variables Railways)
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
REDDIT_SECRET = os.getenv("REDDIT_SECRET")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DROPBOX_ACCESS_TOKEN = os.getenv("DROPBOX_ACCESS_TOKEN")

# Initialisation de Dropbox
dropbox_client = dropbox.Dropbox(DROPBOX_ACCESS_TOKEN)

# Fonction pour lire config.json depuis Dropbox
def load_config_from_dropbox():
    try:
        _, res = dropbox_client.files_download("/config.json")
        config = json.loads(res.content)
        return config
    except Exception as e:
        print(f"Erreur lors du chargement de config.json : {e}")
        return None

# Charger la configuration depuis Dropbox
config = load_config_from_dropbox()
if not config:
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
    except dropbox.exceptions.ApiError:
        dropbox_client.files_upload(json.dumps([]).encode(), path)

init_dropbox_file(LOGS_FILE)
init_dropbox_file(POSTS_FILE)

# Lecture/Écriture sur Dropbox
def read_from_dropbox(path):
    _, res = dropbox_client.files_download(path)
    return json.loads(res.content)

def write_to_dropbox(path, data):
    dropbox_client.files_upload(json.dumps(data).encode(), path, mode=dropbox.files.WriteMode.overwrite)

# Détection et envoi des médias
def send_media(chat_id, submission):
    if hasattr(submission, "post_hint"):
        if submission.post_hint == "image":
            telegram_bot.send_photo(chat_id=chat_id, photo=submission.url, caption=submission.title)
        elif submission.post_hint == "hosted:video" and hasattr(submission, "media"):
            video_url = submission.media["reddit_video"]["fallback_url"]
            telegram_bot.send_video(chat_id=chat_id, video=video_url, caption=submission.title)
        elif submission.post_hint in ["rich:video", "link"] and ".gif" in submission.url:
            telegram_bot.send_animation(chat_id=chat_id, animation=submission.url, caption=submission.title)

# Fonction principale pour surveiller Reddit
def monitor_reddit():
    posted_ids = set(read_from_dropbox(POSTS_FILE))

    while True:
        for subreddit in SUBREDDITS:
            try:
                for submission in reddit.subreddit(subreddit).new(limit=10):
                    if submission.id not in posted_ids:
                        if hasattr(submission, "post_hint") and submission.post_hint in ["image", "hosted:video", "rich:video", "link"]:
                            send_media(TELEGRAM_CHAT_ID, submission)

                        posted_ids.add(submission.id)
                        write_to_dropbox(POSTS_FILE, list(posted_ids))
            except Exception as e:
                log_error(str(e))

# Enregistrement des erreurs
def log_error(message):
    logs = read_from_dropbox(LOGS_FILE)
    logs.append({"error": message})
    write_to_dropbox(LOGS_FILE, logs)

# Commande start
async def start(update: Update, context: CallbackContext):
    await update.message.reply_text(
        "🤖 Bienvenue dans le bot Reddit Telegram !\n\n"
        "Ce bot surveille les subreddits suivants et publie automatiquement les nouveaux posts contenant des images, vidéos et GIFs :\n\n"
        f"{', '.join(SUBREDDITS)}\n\n"
        "Les posts sont publiés directement dans ce groupe ou cette conversation. Profitez-en !"
    )

# Initialisation du bot Telegram
def main():
    updater = Updater(token=TELEGRAM_TOKEN, use_context=True)

    # Gestionnaire pour la commande /start
    dispatcher = updater.dispatcher
    dispatcher.add_handler(CommandHandler("start", start))

    # Lancer la surveillance Reddit en thread séparé
    Thread(target=monitor_reddit, daemon=True).start()

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()