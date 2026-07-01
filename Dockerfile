# ============================================================
#  PARALLAX Backend — Dockerfile de production
#  Flask 3.0 · Python 3.11 · Gunicorn · SQLite / PostgreSQL
# ============================================================
#
#  Ce Dockerfile est conçu pour un déploiement en production sur
#  une VM Linux (x86_64 ou ARM64) dans un cluster local.
#
#  Points clés :
#    - Étape de build séparée pour réduire la taille de l'image finale
#    - L'application s'exécute avec un utilisateur non-root (sécurité)
#    - libmagic est installée (requis par python-magic pour valider les MIME)
#    - Gunicorn remplace le serveur de développement Werkzeug
#    - flask db upgrade est exécuté au démarrage (migrations automatiques)
#    - Le répertoire de stockage /data/parallax/storage est créé et chown
# ============================================================

# ── Étape 1 : build (installation des dépendances) ─────────────────────────
FROM python:3.11-slim-bookworm AS builder

# Dossier de travail temporaire pour assembler les packages
WORKDIR /build

# Copier uniquement le fichier de dépendances (optimise le cache Docker :
# cette couche ne sera reconstruite que si requirements.txt change)
COPY requirements.txt .

# Installer les dépendances dans un répertoire isolé (/install)
# --no-cache-dir  → pas de cache pip dans l'image
# --prefix        → installe dans /install pour la copie dans l'étape suivante
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Étape 2 : image finale (légère, sans outils de build) ──────────────────
FROM python:3.11-slim-bookworm AS final

# ---------- Métadonnées ----------
LABEL maintainer="ENSPY – Département Génie Informatique" \
      description="PARALLAX Backend REST API – Flask 3.0 / Gunicorn" \
      version="1.0"

# ---------- Dépendances système ----------
# libmagic1   → requise par python-magic (validation MIME des uploads)
# curl        → utile pour les health-checks Docker / orchestrateurs
# Nettoyage du cache apt en fin de ligne pour réduire la taille de la couche
RUN apt-get update && apt-get install -y --no-install-recommends \
        libmagic1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ---------- Copie des packages Python depuis l'étape de build ----------
COPY --from=builder /install /usr/local

# Installer Gunicorn séparément (serveur WSGI de production)
# Il n'est pas dans requirements.txt car il ne sert qu'en production
RUN pip install --no-cache-dir gunicorn==22.0.0

# ---------- Utilisateur non-root (sécurité) ----------
# Créer un groupe et un utilisateur dédiés à l'application
# -r  → compte système (pas de répertoire home, pas de shell de login)
RUN groupadd -r parallax && useradd -r -g parallax -s /sbin/nologin parallax

# ---------- Répertoire de travail de l'application ----------
WORKDIR /app

# Copier le code source de l'application
# Le .dockerignore doit exclure : .venv, .git, __pycache__, .env, instance/
COPY . .

# ---------- Répertoire de stockage persistant ----------
# /data/parallax/storage accueillera les fichiers uploadés par les chercheurs.
# Ce chemin doit être monté en volume Docker pour la persistance des données.
RUN mkdir -p /data/parallax/storage \
    && chown -R parallax:parallax /data/parallax /app

# ---------- Port exposé ----------
# Gunicorn écoutera sur ce port à l'intérieur du conteneur
EXPOSE 5000

# ---------- Script d'entrée ----------
# Copier le script de démarrage et le rendre exécutable
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && chown parallax:parallax /usr/local/bin/docker-entrypoint.sh

# Basculer vers l'utilisateur non-root pour l'exécution
USER parallax

# ---------- Healthcheck ----------
# Docker vérifie l'état du conteneur toutes les 30 secondes.
# Le conteneur est considéré "healthy" si /api/docs/ répond HTTP 200.
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:5000/api/docs/ || exit 1

# ---------- Point d'entrée ----------
# Le script docker-entrypoint.sh exécute les migrations puis lance Gunicorn
ENTRYPOINT ["docker-entrypoint.sh"]
