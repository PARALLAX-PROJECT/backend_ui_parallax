import os
from pathlib import Path
from datetime import timedelta


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///parallax.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }

    JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "jwt-dev-secret-change-me")
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=1)
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=7)
    JWT_ALGORITHM = "HS256"

    # --- Stockage fichiers ---
    STORAGE_ROOT = Path(os.environ.get("STORAGE_ROOT", "./storage/projects")).resolve()
    # Taille max d'un upload HTTP (100 Mo)
    MAX_CONTENT_LENGTH = 100 * 1024 * 1024
    # Extensions autorisées pour les sources de calcul
    ALLOWED_SOURCE_EXTENSIONS = {
        ".py", ".c", ".cpp", ".h", ".hpp",
        ".java", ".sh", ".f90", ".f", ".r", ".R",
    }
    ALLOWED_ARCHIVE_EXTENSIONS = {".zip", ".tar", ".gz", ".tgz"}
    # Taille max d'un fichier source décompressé (500 Mo)
    MAX_UNCOMPRESSED_SIZE = 500 * 1024 * 1024
    # Nombre max de fichiers dans une archive
    MAX_FILES_IN_ARCHIVE = 1000
    # Quota de stockage par utilisateur (1 Go)
    MAX_STORAGE_PER_USER = int(os.environ.get("MAX_STORAGE_PER_USER", 1024 * 1024 * 1024))

    # --- CORS ---
    CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",")

    # --- Clé interne cluster (partagée avec les agents) ---
    CLUSTER_INTERNAL_KEY = os.environ.get("CLUSTER_INTERNAL_KEY", "cluster-internal-dev")

    # --- Dispatch TCP vers les agents C ---
    # Port sur lequel le contrôleur écoute les requêtes DISCOVER_MASTER
    CONTROLLER_DISPATCH_PORT = int(os.environ.get("CONTROLLER_DISPATCH_PORT", 9001))
    # Port sur lequel le maître écoute les soumissions de programmes
    MASTER_DISPATCH_PORT = int(os.environ.get("MASTER_DISPATCH_PORT", 9000))
    # Timeout (secondes) pour toute connexion TCP vers les agents
    DISPATCH_TIMEOUT_S = float(os.environ.get("DISPATCH_TIMEOUT_S", 10.0))
    # Constantes structure program_message_t — doivent correspondre au code C agent
    PROG_NAME_MAX = int(os.environ.get("PROG_NAME_MAX", 256))
    PROG_CODE_MAX = int(os.environ.get("PROG_CODE_MAX", 1_048_576))  # 1 Mo
    # Si False (défaut dev) : soumettre même sans cluster configuré (avertissement)
    # Si True  (prod)       : refuser la soumission si aucun maître n'est joignable
    DISPATCH_REQUIRED = os.environ.get("DISPATCH_REQUIRED", "false").lower() == "true"

    # --- Paramètres heartbeat ---
    HB_PERIOD_S = 2           # période unicast heartbeat
    HB_SUSPECT_THRESHOLD_S = 4  # délai avant SUSPECTED
    HB_FAILED_THRESHOLD_S = 8   # délai avant FAILED
    HB_OVERLOAD_HIGH = 0.85   # seuil surcharge (CPU ou RAM)
    HB_OVERLOAD_LOW = 0.65    # seuil retour charge normale

    # --- Tentatives max sur une sous-tâche avant échec définitif ---
    MAX_TASK_ATTEMPTS = 3


class DevelopmentConfig(Config):
    DEBUG = True
    SQLALCHEMY_ECHO = False


class ProductionConfig(Config):
    DEBUG = False
    # En prod, forcer HTTPS
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True


_config_map = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}


def get_config() -> type:
    env = os.environ.get("FLASK_ENV", "default")
    return _config_map.get(env, DevelopmentConfig)
