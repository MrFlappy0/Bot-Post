# Utilise une image Python légère
FROM python:3.9-slim

# Définir le répertoire de travail
WORKDIR /app

# Copier les dépendances
COPY requirements.txt .

# Installer les dépendances
RUN pip install --no-cache-dir -r requirements.txt

# Copier le code du bot
COPY bot/ .

# Commande pour exécuter le bot
CMD ["python", "main.py"]