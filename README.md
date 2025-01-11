# Reddit Media Bot

## Description
Le Reddit Media Bot est un bot Telegram qui surveille des subreddits spécifiques pour récupérer des images, vidéos et GIFs, puis les envoie automatiquement aux abonnés sur Telegram. Il génère également des rapports quotidiens sur l'activité du bot.

## Fonctionnalités
- Surveillance de subreddits pour récupérer des médias.
- Envoi automatique des médias aux abonnés Telegram.
- Génération de rapports quotidiens.
- Gestion des abonnés et des subreddits via Dropbox.
- Journalisation détaillée des opérations.

## Prérequis
- Python 3.7+
- Compte Reddit avec une application configurée pour obtenir les identifiants API.
- Bot Telegram avec un token d'accès.
- Compte Dropbox avec un token d'accès.

## Installation
1. Clonez le dépôt :
    ```bash
    git clone https://github.com/MrFlappy0/Bot-Post.git
    cd Bot-Post
    ```

2. Installez les dépendances :
    ```bash
    pip install -r requirements.txt
    ```

3. Configurez les variables d'environnement :
    ```bash
    export REDDIT_CLIENT_ID="votre_client_id"
    export REDDIT_SECRET="votre_secret"
    export REDDIT_USER_AGENT="votre_user_agent"
    export TELEGRAM_TOKEN="votre_telegram_token"
    export DROPBOX_ACCESS_TOKEN="votre_dropbox_token"
    export ADMIN_CHAT_ID="votre_chat_id_admin"
    ```

## Utilisation
1. Démarrez le bot :
    ```bash
    python bot.py
    ```

2. Commandes Telegram disponibles :
    - `/start` : Affiche un message de bienvenue et enregistre l'utilisateur.
    - `/help` : Affiche un message d'aide détaillé.
    - `/stats` : Affiche les statistiques actuelles des médias envoyés.
    - `/reload` : Recharge les données des abonnés et des subreddits depuis Dropbox.
    - `/clean_temp` : Nettoie manuellement le répertoire temporaire.

## Structure du Projet
- `bot.py` : Contient le code principal du bot.
- `requirements.txt` : Liste des dépendances Python.
- `Procfile` : Fichier de configuration pour déploiement sur des plateformes comme Heroku.
- `README.md` : Ce fichier, contenant la documentation du projet.

## Journalisation
Les logs sont enregistrés dans un fichier `bot.log` et affichés dans la console. Les logs incluent des informations sur les opérations réussies et les erreurs.

## Déploiement
Pour déployer le bot sur une plateforme comme Heroku, assurez-vous que toutes les variables d'environnement sont correctement configurées et que le fichier `Procfile` est présent.

## Contribuer
Les contributions sont les bienvenues ! Veuillez soumettre une pull request ou ouvrir une issue pour discuter des changements que vous souhaitez apporter.

## Licence
Ce projet est sous licence MIT. Voir le fichier [LICENSE](LICENSE) pour plus de détails.
