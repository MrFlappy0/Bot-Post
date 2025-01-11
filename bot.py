import praw
from telegram import Bot
import time

# Configuration Reddit
REDDIT_CLIENT_ID = 'votre_client_id'
REDDIT_CLIENT_SECRET = 'votre_client_secret'
REDDIT_USER_AGENT = 'bot_telegram_reddit'

# Configuration Telegram
TELEGRAM_TOKEN = 'votre_telegram_token'
TELEGRAM_CHAT_ID = 'votre_chat_id'

# Subreddits à surveiller
SUBREDDITS = ['Nudes', 'FantasticBreasts', 'GoneWild', 'cumsluts', 'PetiteGoneWild', 'RealGirls', 'nsfw', 'Amateur', 'pregnantporn','NSFW_GIF', 'scrubsgonewild', 'GoneWildPlus', 'NaughtyWives', 'snapleaks', 'pregnantonlyfans', 'Nude_Selfie', 'Puffies' ]
# Initialiser Reddit avec PRAW
reddit = praw.Reddit(
    client_id=REDDIT_CLIENT_ID,
    client_secret=REDDIT_CLIENT_SECRET,
    user_agent=REDDIT_USER_AGENT
)

# Initialiser le bot Telegram
bot = Bot(token=TELEGRAM_TOKEN)

# Mémoriser les IDs des posts déjà envoyés
sent_posts = set()

def fetch_and_send_new_posts():
    for subreddit_name in SUBREDDITS:
        subreddit = reddit.subreddit(subreddit_name)
        for submission in subreddit.new(limit=5):  # Vérifie les 5 derniers posts
            if submission.id not in sent_posts:
                # Ajouter l'ID du post dans sent_posts
                sent_posts.add(submission.id)

                # Préparer le contenu à envoyer
                if submission.url.endswith(('.jpg', '.png', '.gif', '.mp4', '.webm')):
                    content = f"**[{submission.title}]({submission.url})**\n\nVia r/{subreddit_name}"
                    bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=content, parse_mode="Markdown")
                elif submission.is_self:
                    content = f"**{submission.title}**\n\n{submission.selftext}\n\nVia r/{subreddit_name}"
                    bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=content, parse_mode="Markdown")
                else:
                    content = f"**{submission.title}**\n\n[Voir le post sur Reddit]({submission.url})\n\nVia r/{subreddit_name}"
                    bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=content, parse_mode="Markdown")

if __name__ == "__main__":
    print("Bot démarré !")
    while True:
        fetch_and_send_new_posts()
        time.sleep(10)  # Vérifie toutes les 60 secondes