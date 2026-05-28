# PARALLAX — Backend API

Backend REST de l'infrastructure de calcul distribué **PARALLAX**, développé pour l'[ENSPY](https://www.enspy.cm) (École Nationale Supérieure Polytechnique de Yaoundé).

PARALLAX fédère les machines hétérogènes sous-utilisées du campus en un supercalculateur local, sans dépendance à Internet. Les chercheurs déposent leur code source annoté via cette interface web ; le cluster se charge de la décomposition, de l'exécution distribuée et de la restitution des résultats.

---

## Fonctionnalités

- **Authentification JWT** — inscription, connexion, rafraîchissement de token, révocation par liste noire en base de données
- **Gestion de projets** — upload sécurisé de code source ou d'archives, soumission pour exécution, suivi de la progression en temps réel, téléchargement des résultats
- **Supervision du cluster** — liste des nœuds, métriques CPU/RAM en temps réel, historique des heartbeats, mode maintenance, retrait de nœuds
- **API interne cluster** — enregistrement des agents C, réception des heartbeats (T_HB = 2 s), distribution des tâches aux workers, logique de retry automatique
- **Détection de pannes** — thread de surveillance background : ALIVE → SUSPECTED (4 s) → FAILED (8 s)
- **Stockage sécurisé** — anti path-traversal, protection zip bomb (500 Mo décompressés max), quota par utilisateur (1 Go), écriture atomique
- **Documentation API** — Swagger UI complète accessible à `/api/docs/`

---

## Architecture

```
┌──────────────────────────────────────────────────┐
│          Interface Web (React + Vite)            │
│   Chercheur · Gestionnaire                       │
└──────────────────────┬───────────────────────────┘
                       │ HTTPS / JWT
                       ▼
┌──────────────────────────────────────────────────┐
│           Backend Flask  (ce dépôt)              │
│  /api/auth/*        Authentification             │
│  /api/tasks/*       Projets chercheur            │
│  /api/nodes/*       Supervision cluster          │
│  /api/programmes/*  Vue gestionnaire             │
│  /api/cluster/*     API interne agents C         │
└──────────────────────┬───────────────────────────┘
                       │ X-Cluster-Key
                       ▼
┌──────────────────────────────────────────────────┐
│           Agents C (nœuds du cluster)            │
│  Agent Maître   · Agent Worker · Contrôleur      │
└──────────────────────────────────────────────────┘
```

### Matériel cible

| Machine | CPU | RAM | OS |
|---|---|---|---|
| Dell OptiPlex 380 (×2) | Intel Core 2 Duo E7500 @ 2.93 GHz | 4 Go / 2 Go DDR3 | Ubuntu 18.04 |
| Dell System 360 | Intel Core 2 Duo | 2 Go DDR2 | Ubuntu 18.04 |
| Wyse Thin Client (×3) | VIA/AMD 533–1000 MHz | 32–64 Mo | Windows CE |

---

## Stack technique

- **Python 3.10+** / **Flask 3.0**
- **SQLAlchemy 2.0** + **Flask-Migrate** (Alembic)
- **Flask-JWT-Extended** — access token (15 min) + refresh token (30 j)
- **Flask-CORS**
- **flasgger** — OpenAPI 3.0 / Swagger UI
- **python-magic** — validation MIME des fichiers uploadés
- **SQLite** (développement) / **PostgreSQL** (production recommandé)

---

## Installation

```bash
# Cloner le dépôt
git clone https://github.com/<votre-org>/parallax-backend.git
cd parallax-backend

# Environnement virtuel
python -m venv .venv
source .venv/bin/activate  # Windows : .venv\Scripts\activate

# Dépendances
pip install -r requirements.txt

# Configuration
cp .env.example .env
# Éditer .env avec vos valeurs
```

### Variables d'environnement (`.env`)

| Variable | Description | Exemple |
|---|---|---|
| `SECRET_KEY` | Clé secrète Flask | `changeme-prod-secret` |
| `JWT_SECRET_KEY` | Clé de signature JWT | `changeme-jwt-secret` |
| `DATABASE_URL` | URL SQLAlchemy | `sqlite:///parallax.db` |
| `STORAGE_ROOT` | Répertoire de stockage des fichiers | `/data/parallax/storage` |
| `CLUSTER_INTERNAL_KEY` | Clé partagée avec les agents C | `super-secret-cluster-key` |
| `CORS_ORIGINS` | Origines autorisées (séparées par virgule) | `http://localhost:3000` |
| `MAX_STORAGE_PER_USER` | Quota disque par utilisateur (octets) | `1073741824` (1 Go) |
| `MASTER_NODE_IP` | IP du nœud hébergeant ce backend | `192.168.1.100` |

---

## Démarrage

```bash
# Initialiser la base de données
flask db upgrade

# Lancer en développement
python run.py
```

Le serveur démarre sur `http://0.0.0.0:5000`.

---

## Documentation API

Une fois le serveur lancé, la Swagger UI est disponible à :

```
http://localhost:5000/api/docs/
```

La spécification OpenAPI 3.0 brute (JSON) est accessible à :

```
http://localhost:5000/api/openapi.json
```

Elle documente les 26 endpoints répartis en 5 domaines :

| Domaine | Préfixe | Auth |
|---|---|---|
| Authentification | `/api/auth/` | — / JWT |
| Projets (chercheur) | `/api/tasks/` | JWT Bearer |
| Nœuds (gestionnaire) | `/api/nodes/` | JWT Bearer + rôle gestionnaire |
| Programmes (gestionnaire) | `/api/programmes/` | JWT Bearer + rôle gestionnaire |
| API interne cluster | `/api/cluster/` | `X-Cluster-Key` |

---

## Intégration frontend

Le fichier [`workflow.md`](workflow.md) décrit le workflow complet que le développeur frontend
doit suivre pour communiquer avec ce backend, en précisant à chaque étape
ce que représente chaque appel pour l'utilisateur final.

Le portail React (frontend) est disponible dans un dépôt séparé : **PARALLAX Portal**.

---

## Deux rôles utilisateur

| Rôle | Accès |
|---|---|
| `chercheur` | Dépose son code, suit l'exécution, télécharge les résultats |
| `gestionnaire` | Supervise tous les nœuds, voit tous les programmes, peut annuler |

---

## Cycle de vie d'un programme

```
Import du code
      │
      ▼
   soumis  ──── POST /submit ────►  en_decomposition
                                           │
                              Agent maître crée les sous-tâches
                                           │
                                           ▼
                                       en_cours
                                      /        \
                            toutes OK            au moins 1 échec définitif
                                /                        \
                           termine                      echec
                        (résultats ZIP                (retry épuisé,
                        disponibles)                   re-submit possible)
```

---

## Structure du projet

```
parallax-backend/
├── app/
│   ├── __init__.py          # Factory Flask + init extensions
│   ├── config.py            # Configurations Dev/Prod
│   ├── extensions.py        # db, jwt, cors, migrate
│   ├── swagger_spec.py      # Schémas OpenAPI 3.0
│   ├── api/
│   │   ├── auth.py          # /api/auth/*
│   │   ├── tasks.py         # /api/tasks/*
│   │   ├── nodes.py         # /api/nodes/*
│   │   ├── cluster.py       # /api/cluster/*
│   │   └── programmes_admin.py  # /api/programmes/*
│   ├── models/
│   │   ├── user.py          # User, TokenBlocklist
│   │   ├── node.py          # Node, NodeProfile, Heartbeat
│   │   ├── programme.py     # Programme
│   │   └── tache.py         # TacheAtomique
│   ├── services/
│   │   ├── storage.py       # Gestion sécurisée des fichiers
│   │   └── node_monitor.py  # Thread surveillance heartbeats
│   └── utils/
│       ├── decorators.py    # @gestionnaire_required, @cluster_internal
│       └── responses.py     # Helpers JSON uniformes
├── migrations/              # Alembic
├── workflow.md              # Guide intégration frontend
├── requirements.txt
├── run.py
└── .env.example
```

---

## Sécurité

- Mots de passe hashés avec Werkzeug (PBKDF2-SHA256)
- Tokens JWT révocables via liste noire en base de données
- Protection anti path-traversal sur tous les chemins de fichiers
- Détection de zip bomb (taille décompressée + nombre de fichiers)
- Quota disque par utilisateur appliqué à chaque upload
- Écriture atomique des fichiers (tempfile + `os.replace`)
- Séparation stricte API publique (JWT) / API interne (clé symétrique)

---

## Licence

Projet académique — ENSPY, Département Génie Informatique.
