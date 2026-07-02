#!/bin/sh
# ==============================================================
#  PARALLAX Backend — Script de démarrage du conteneur
#
#  Exécuté par Docker à chaque démarrage du conteneur.
#  Ordre des opérations :
#    1. Attente optionnelle de la base PostgreSQL (si applicable)
#    2. Application des migrations Alembic (flask db upgrade)
#    3. Lancement de Gunicorn en mode production
# ==============================================================

set -e  # Arrêter immédiatement en cas d'erreur

echo "==> [PARALLAX] Démarrage du conteneur..."

# ── 1. Application des migrations de base de données ──────────────────
# flask db upgrade applique toutes les migrations Alembic en attente.
# Cela garantit que le schéma est toujours synchronisé avec le code.
echo "==> [PARALLAX] Application des migrations (flask db upgrade)..."
flask db upgrade
echo "==> [PARALLAX] Migrations appliquées avec succès."

# ── 2. Lancement de Gunicorn ───────────────────────────────────────────
# Gunicorn est le serveur WSGI de production. Il remplace le serveur
# de développement Werkzeug (qui ne doit JAMAIS être utilisé en prod).
#
# Options :
#   --bind 0.0.0.0:5000   → écoute sur tous les interfaces, port 5000
#   --workers            → nombre de processus workers (2 × CPU + 1 recommandé)
#                          ici fixé à 4 pour une machine modeste (Core 2 Duo)
#   --threads            → threads par worker pour gérer les requêtes concurrentes
#   --timeout            → kill un worker si la requête dépasse 120 s
#   --access-logfile -   → logs d'accès sur stdout (récupérés par Docker)
#   --error-logfile -    → logs d'erreur sur stderr
#   --log-level          → niveau de log (info en prod, debug si FLASK_ENV=development)
#   --preload-app        → charge l'app avant le fork (économise la RAM)
#
echo "==> [PARALLAX] Lancement de Gunicorn (workers=4, threads=2)..."
exec gunicorn \
    --bind 0.0.0.0:5000 \
    --workers "${GUNICORN_WORKERS:-2}" \
    --threads "${GUNICORN_THREADS:-1}" \
    --timeout 120 \
    --access-logfile - \
    --error-logfile - \
    --log-level "${GUNICORN_LOG_LEVEL:-info}" \
    --preload \
    "run:create_app()"
