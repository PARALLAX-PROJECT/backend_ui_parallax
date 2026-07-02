"""
Spécification OpenAPI 3.0 complète pour l'API PARALLAX.

Utilisé par flasgger pour générer la Swagger UI à /api/docs
et le JSON brut à /api/openapi.json.
"""

SWAGGER_CONFIG = {
    "headers": [],
    "specs": [
        {
            "endpoint": "apispec",
            "route": "/api/openapi.json",
            "rule_filter": lambda rule: True,
            "model_filter": lambda tag: True,
        }
    ],
    "static_url_path": "/flasgger_static",
    "swagger_ui": True,
    "specs_route": "/api/docs/",
    "openapi": "3.0.3",
}

SWAGGER_TEMPLATE = {
    "openapi": "3.0.3",
    "info": {
        "title": "PARALLAX — API Backend",
        "description": (
            "API REST du backend PARALLAX, l'infrastructure de calcul distribué de l'ENSPY "
            "(École Nationale Supérieure Polytechnique de Yaoundé).\n\n"
            "## Deux domaines d'utilisation\n\n"
            "### Interface utilisateur (chercheurs & gestionnaires)\n"
            "Authentification JWT Bearer. Deux rôles :\n"
            "- **chercheur** : dépose et suit ses projets de calcul\n"
            "- **gestionnaire** : supervise le cluster et tous les programmes\n\n"
            "### API interne cluster (agents C)\n"
            "Authentification via l'en-tête `X-Cluster-Key`. "
            "Les agents (nœud maître, workers) utilisent ces routes pour "
            "s'enregistrer, envoyer des heartbeats et traiter les tâches."
        ),
        "version": "1.0.0",
        "contact": {
            "name": "ENSPY — Département Informatique",
        },
    },
    "servers": [
        {"url": "http://localhost:5000", "description": "Développement local"},
        {"url": "http://master-node:5000", "description": "Nœud maître PARALLAX"},
    ],
    "tags": [
        {
            "name": "Authentification",
            "description": "Inscription, connexion, gestion des tokens JWT.",
        },
        {
            "name": "Projets (Chercheur)",
            "description": (
                "Cycle de vie complet d'un projet : import du code source, "
                "soumission pour exécution distribuée, suivi de la progression, "
                "téléchargement des résultats."
            ),
        },
        {
            "name": "Cluster — Noeuds (Gestionnaire)",
            "description": (
                "Supervision et administration des nœuds du cluster PARALLAX. "
                "Accès réservé aux gestionnaires."
            ),
        },
        {
            "name": "Cluster — Programmes (Gestionnaire)",
            "description": (
                "Vue administrative sur tous les programmes en cours d'exécution, "
                "toutes origines confondues."
            ),
        },
        {
            "name": "Cluster — API Interne",
            "description": (
                "Routes réservées aux agents C du cluster "
                "(nœud maître, workers). "
                "Authentification via `X-Cluster-Key`."
            ),
        },
    ],
    "components": {
        "securitySchemes": {
            "BearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": (
                    "Access token JWT obtenu via `POST /api/auth/login`. "
                    "Durée de vie courte (15 min). "
                    "À rafraîchir avec `POST /api/auth/refresh`."
                ),
            },
            "ClusterKey": {
                "type": "apiKey",
                "in": "header",
                "name": "X-Cluster-Key",
                "description": (
                    "Clé secrète partagée entre le backend et les agents C. "
                    "Configurée via la variable d'environnement `CLUSTER_INTERNAL_KEY`."
                ),
            },
        },
        "schemas": {
            # ── Requêtes d'authentification ────────────────────────────────
            "UserRegisterRequest": {
                "type": "object",
                "required": ["username", "email", "password"],
                "properties": {
                    "username": {
                        "type": "string",
                        "minLength": 3,
                        "maxLength": 80,
                        "pattern": "^[a-zA-Z0-9_.-]{3,80}$",
                        "example": "alice_dupont",
                        "description": "Identifiant unique. Caractères autorisés : lettres, chiffres, _, ., -",
                    },
                    "email": {
                        "type": "string",
                        "format": "email",
                        "example": "alice@enspy.cm",
                        "description": "Adresse e-mail valide et unique.",
                    },
                    "password": {
                        "type": "string",
                        "minLength": 8,
                        "format": "password",
                        "example": "s3cr3t!Pass",
                        "description": "Mot de passe d'au moins 8 caractères.",
                    },
                    "role": {
                        "type": "string",
                        "enum": ["chercheur", "gestionnaire"],
                        "default": "chercheur",
                        "description": "Rôle attribué au compte.",
                    },
                },
            },
            "UserLoginRequest": {
                "type": "object",
                "required": ["password"],
                "properties": {
                    "username": {
                        "type": "string",
                        "example": "alice_dupont",
                        "description": "Nom d'utilisateur OU adresse e-mail.",
                    },
                    "email": {
                        "type": "string",
                        "format": "email",
                        "example": "alice@enspy.cm",
                        "description": "Alternative à `username`.",
                    },
                    "password": {
                        "type": "string",
                        "format": "password",
                        "example": "s3cr3t!Pass",
                    },
                },
            },
            # ── Réponses d'authentification ────────────────────────────────
            "UserResponse": {
                "type": "object",
                "description": "Profil public d'un utilisateur PARALLAX.",
                "properties": {
                    "id": {
                        "type": "string",
                        "format": "uuid",
                        "example": "550e8400-e29b-41d4-a716-446655440000",
                    },
                    "username": {"type": "string", "example": "alice_dupont"},
                    "email": {"type": "string", "format": "email", "example": "alice@enspy.cm"},
                    "role": {
                        "type": "string",
                        "enum": ["chercheur", "gestionnaire"],
                        "example": "chercheur",
                    },
                    "is_active": {"type": "boolean", "example": True},
                    "storage_used_bytes": {
                        "type": "integer",
                        "example": 15728640,
                        "description": "Espace disque utilisé par cet utilisateur (en octets).",
                    },
                    "created_at": {
                        "type": "string",
                        "format": "date-time",
                        "example": "2024-09-01T08:00:00Z",
                    },
                    "last_login_at": {
                        "type": "string",
                        "format": "date-time",
                        "nullable": True,
                        "example": "2024-09-15T14:32:00Z",
                    },
                },
            },
            "AuthTokensResponse": {
                "type": "object",
                "description": "Paire de tokens JWT retournée à la connexion.",
                "properties": {
                    "access_token": {
                        "type": "string",
                        "description": "Token d'accès JWT (durée courte, ~15 min).",
                        "example": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                    },
                    "refresh_token": {
                        "type": "string",
                        "description": "Token de rafraîchissement JWT (durée longue, ~30 j).",
                        "example": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                    },
                    "user": {"$ref": "#/components/schemas/UserResponse"},
                },
            },
            "RefreshTokenResponse": {
                "type": "object",
                "properties": {
                    "access_token": {
                        "type": "string",
                        "description": "Nouvel access token JWT.",
                        "example": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                    }
                },
            },
            # ── Programmes ─────────────────────────────────────────────────
            "ProgrammeProgress": {
                "type": "object",
                "description": "Compteurs d'avancement des sous-tâches atomiques.",
                "properties": {
                    "total": {"type": "integer", "example": 12, "description": "Nombre total de sous-tâches."},
                    "done": {"type": "integer", "example": 8, "description": "Sous-tâches terminées avec succès."},
                    "failed": {"type": "integer", "example": 1, "description": "Sous-tâches en échec définitif."},
                    "pending": {"type": "integer", "example": 3, "description": "Sous-tâches encore actives."},
                    "percent": {
                        "type": "number",
                        "format": "float",
                        "example": 66.7,
                        "description": "Pourcentage d'avancement (done / total × 100).",
                    },
                },
            },
            "ProgrammeResponse": {
                "type": "object",
                "description": "Représentation complète d'un programme de calcul.",
                "properties": {
                    "id": {
                        "type": "string",
                        "format": "uuid",
                        "example": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                    },
                    "name": {
                        "type": "string",
                        "example": "Simulation Monte-Carlo — turbulence",
                        "description": "Nom lisible donné au projet.",
                    },
                    "description": {
                        "type": "string",
                        "nullable": True,
                        "example": "Modèle de Navier-Stokes simplifié pour analyse de flux laminaire.",
                    },
                    "status": {
                        "type": "string",
                        "enum": [
                            "soumis",
                            "en_decomposition",
                            "en_cours",
                            "termine",
                            "echec",
                            "annule",
                        ],
                        "example": "en_cours",
                        "description": (
                            "Cycle de vie du programme :\n"
                            "- `soumis` : uploadé, en attente de soumission explicite\n"
                            "- `en_decomposition` : l'agent maître analyse les annotations\n"
                            "- `en_cours` : des workers exécutent les sous-tâches\n"
                            "- `termine` : tous les résultats disponibles\n"
                            "- `echec` : au moins une sous-tâche a épuisé ses tentatives\n"
                            "- `annule` : interrompu par l'utilisateur ou le gestionnaire"
                        ),
                    },
                    "original_filename": {
                        "type": "string",
                        "example": "simulation.zip",
                        "description": "Nom du fichier uploadé à l'origine.",
                    },
                    "source_size_bytes": {
                        "type": "integer",
                        "nullable": True,
                        "example": 524288,
                        "description": "Taille des sources (en octets).",
                    },
                    "result_size_bytes": {
                        "type": "integer",
                        "nullable": True,
                        "example": 131072,
                        "description": "Taille cumulée des résultats (en octets).",
                    },
                    "error_message": {
                        "type": "string",
                        "nullable": True,
                        "example": "2/12 sous-tâches ont échoué après 3 tentatives.",
                    },
                    "uploaded_at": {"type": "string", "format": "date-time"},
                    "submitted_at": {"type": "string", "format": "date-time", "nullable": True},
                    "started_at": {"type": "string", "format": "date-time", "nullable": True},
                    "completed_at": {"type": "string", "format": "date-time", "nullable": True},
                    "progress": {
                        "allOf": [{"$ref": "#/components/schemas/ProgrammeProgress"}],
                        "nullable": True,
                        "description": "Présent uniquement si `include_progress=true` (par défaut dans les listes).",
                    },
                },
            },
            "PaginatedProgrammesResponse": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/ProgrammeResponse"},
                    },
                    "total": {"type": "integer", "example": 47},
                    "page": {"type": "integer", "example": 1},
                    "per_page": {"type": "integer", "example": 20},
                    "pages": {"type": "integer", "example": 3},
                },
            },
            # ── Tâches atomiques ───────────────────────────────────────────
            "TacheAtomiqueResponse": {
                "type": "object",
                "description": "Sous-tâche atomique issue de la décomposition d'un programme.",
                "properties": {
                    "id": {"type": "string", "format": "uuid"},
                    "programme_id": {"type": "string", "format": "uuid"},
                    "annotation_id": {
                        "type": "string",
                        "nullable": True,
                        "example": "split_bloc_0",
                        "description": "Identifiant de l'annotation source (@parallax.split, @parallax.dag…).",
                    },
                    "function_name": {
                        "type": "string",
                        "nullable": True,
                        "example": "compute_turbulence_slice",
                        "description": "Nom de la fonction annotée dans le code source.",
                    },
                    "status": {
                        "type": "string",
                        "enum": [
                            "en_attente",
                            "assignee",
                            "en_cours",
                            "terminee",
                            "echouee",
                            "migree",
                        ],
                        "example": "terminee",
                        "description": (
                            "- `en_attente` : en file d'attente\n"
                            "- `assignee` : réservée par un worker\n"
                            "- `en_cours` : en exécution\n"
                            "- `terminee` : résultat disponible\n"
                            "- `echouee` : épuisement des tentatives\n"
                            "- `migree` : réassignée après panne du worker"
                        ),
                    },
                    "worker_node_uuid": {
                        "type": "string",
                        "nullable": True,
                        "description": "UUID du nœud worker actuellement assigné.",
                    },
                    "attempts": {
                        "type": "integer",
                        "example": 1,
                        "description": "Nombre de tentatives d'exécution.",
                    },
                    "max_attempts": {"type": "integer", "example": 3},
                    "data_input": {
                        "type": "string",
                        "nullable": True,
                        "description": "Données d'entrée sérialisées (JSON texte).",
                    },
                    "data_output": {
                        "type": "string",
                        "nullable": True,
                        "description": "Résultat sérialisé (JSON texte). Rempli après `terminee`.",
                    },
                    "error_message": {"type": "string", "nullable": True},
                    "created_at": {"type": "string", "format": "date-time"},
                    "assigned_at": {"type": "string", "format": "date-time", "nullable": True},
                    "started_at": {"type": "string", "format": "date-time", "nullable": True},
                    "completed_at": {"type": "string", "format": "date-time", "nullable": True},
                },
            },
            # ── Noeuds ────────────────────────────────────────────────────
            "NodeProfileData": {
                "type": "object",
                "description": "Profil matériel statique d'un nœud.",
                "properties": {
                    "cpu_cores": {"type": "integer", "example": 2, "description": "Nombre de cœurs CPU."},
                    "cpu_freq_mhz": {"type": "number", "example": 2933.0, "description": "Fréquence CPU en MHz."},
                    "arch_cpu": {"type": "string", "example": "x86_64"},
                    "ram_total_mb": {"type": "integer", "example": 4096, "description": "RAM totale (Mo)."},
                    "ram_available_mb": {
                        "type": "integer",
                        "example": 3200,
                        "description": "RAM disponible au dernier heartbeat (Mo).",
                    },
                    "storage_total_gb": {"type": "number", "example": 160.0, "description": "Stockage total (Go)."},
                    "storage_available_gb": {"type": "number", "example": 80.5},
                    "network_latency_ms": {
                        "type": "number",
                        "example": 1.2,
                        "description": "Latence réseau vers le maître (ms).",
                    },
                    "os_info": {
                        "type": "string",
                        "nullable": True,
                        "example": "Ubuntu 18.04.6 LTS",
                    },
                },
            },
            "NodeResponse": {
                "type": "object",
                "description": "État complet d'un nœud du cluster PARALLAX.",
                "properties": {
                    "uuid": {
                        "type": "string",
                        "format": "uuid",
                        "example": "node-dell-01",
                        "description": "Identifiant unique du nœud (fourni par l'agent).",
                    },
                    "ip": {"type": "string", "example": "192.168.1.101"},
                    "hostname": {"type": "string", "nullable": True, "example": "dell-optiplex-01"},
                    "role": {
                        "type": "string",
                        "enum": ["master", "worker", "controller", "remplacant"],
                        "example": "worker",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["actif", "surcharge", "en_panne", "en_maintenance", "eteint"],
                        "example": "actif",
                        "description": (
                            "- `actif` : disponible pour recevoir des tâches\n"
                            "- `surcharge` : CPU/RAM > 85 %\n"
                            "- `en_panne` : aucun heartbeat depuis > 8 s\n"
                            "- `en_maintenance` : mis hors rotation par le gestionnaire\n"
                            "- `eteint` : retiré définitivement du cluster"
                        ),
                    },
                    "current_cpu_usage": {
                        "type": "number",
                        "format": "float",
                        "example": 0.42,
                        "description": "Utilisation CPU entre 0.0 et 1.0.",
                    },
                    "current_ram_usage": {"type": "number", "format": "float", "example": 0.61},
                    "current_tasks_count": {"type": "integer", "example": 2},
                    "current_score": {
                        "type": "number",
                        "format": "float",
                        "example": 0.73,
                        "description": "Score d'élection calculé (formule pondérée CPU/RAM/latence/fréquence).",
                    },
                    "last_heartbeat_at": {
                        "type": "string",
                        "format": "date-time",
                        "nullable": True,
                        "example": "2024-09-15T14:32:05Z",
                    },
                    "registered_at": {"type": "string", "format": "date-time"},
                    "profile": {
                        "allOf": [{"$ref": "#/components/schemas/NodeProfileData"}],
                        "nullable": True,
                        "description": "Profil matériel (présent si include_profile=true).",
                    },
                },
            },
            "PaginatedNodesResponse": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/NodeResponse"},
                    },
                    "total": {"type": "integer", "example": 6},
                    "page": {"type": "integer", "example": 1},
                    "per_page": {"type": "integer", "example": 50},
                    "pages": {"type": "integer", "example": 1},
                },
            },
            "ClusterStatsResponse": {
                "type": "object",
                "properties": {
                    "nodes": {
                        "type": "object",
                        "properties": {
                            "total": {"type": "integer", "example": 6},
                            "actifs": {"type": "integer", "example": 4},
                            "surcharges": {"type": "integer", "example": 1},
                            "en_panne": {"type": "integer", "example": 0},
                            "en_maintenance": {"type": "integer", "example": 1},
                        },
                    },
                    "tasks": {
                        "type": "object",
                        "properties": {
                            "en_cours": {"type": "integer", "example": 7},
                            "en_attente": {"type": "integer", "example": 3},
                        },
                    },
                },
            },
            # ── API Interne Cluster ────────────────────────────────────────
            "NodeRegisterRequest": {
                "type": "object",
                "required": ["uuid", "ip"],
                "description": "Payload envoyé par l'agent C au démarrage (algorithme 1 du rapport).",
                "properties": {
                    "uuid": {
                        "type": "string",
                        "example": "node-dell-01",
                        "description": "Identifiant unique généré par l'agent.",
                    },
                    "ip": {
                        "type": "string",
                        "example": "192.168.1.101",
                        "description": "Adresse IP de l'interface réseau principale.",
                    },
                    "hostname": {"type": "string", "nullable": True, "example": "dell-optiplex-01"},
                    "role": {
                        "type": "string",
                        "enum": ["master", "worker", "controller", "remplacant"],
                        "default": "worker",
                    },
                    "profile": {
                        "type": "object",
                        "description": "Profil matériel collecté au démarrage.",
                        "properties": {
                            "cpu_cores": {"type": "integer", "example": 2},
                            "cpu_freq_mhz": {"type": "number", "example": 2933.0},
                            "arch_cpu": {"type": "string", "example": "x86_64"},
                            "ram_total_mb": {"type": "integer", "example": 4096},
                            "ram_available_mb": {"type": "integer", "example": 3800},
                            "storage_total_gb": {"type": "number", "example": 160.0},
                            "storage_available_gb": {"type": "number", "example": 80.5},
                            "network_latency_ms": {"type": "number", "example": 1.2},
                            "os_info": {"type": "string", "example": "Ubuntu 18.04.6 LTS"},
                        },
                    },
                },
            },
            "HeartbeatRequest": {
                "type": "object",
                "required": ["uuid"],
                "description": (
                    "Structure HBi,t = (uuid_i, k_i·t, CPU_i·t, RAM_i·t, q_i·t, Score_i·t) "
                    "— §2.3.11 du rapport."
                ),
                "properties": {
                    "uuid": {"type": "string", "example": "node-dell-01"},
                    "cpu_usage": {
                        "type": "number",
                        "format": "float",
                        "minimum": 0.0,
                        "maximum": 1.0,
                        "example": 0.42,
                        "description": "Taux d'utilisation CPU (0.0–1.0).",
                    },
                    "ram_usage": {
                        "type": "number",
                        "format": "float",
                        "minimum": 0.0,
                        "maximum": 1.0,
                        "example": 0.61,
                    },
                    "tasks_in_progress": {
                        "type": "integer",
                        "example": 2,
                        "description": "Nombre de sous-tâches en cours d'exécution sur ce nœud.",
                    },
                    "score": {
                        "type": "number",
                        "example": 0.73,
                        "description": "Score d'élection auto-calculé par l'agent.",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["actif", "surcharge", "en_panne", "en_maintenance"],
                        "example": "actif",
                    },
                    "ram_available_mb": {
                        "type": "integer",
                        "example": 3200,
                        "description": "RAM libre courante (met à jour le profil).",
                    },
                    "network_latency_ms": {
                        "type": "number",
                        "example": 1.5,
                        "description": "Latence mesurée vers le maître (met à jour le profil).",
                    },
                },
            },
            "TaskResultRequest": {
                "type": "object",
                "required": ["node_uuid"],
                "properties": {
                    "node_uuid": {
                        "type": "string",
                        "example": "node-dell-02",
                        "description": "UUID du worker qui a exécuté la tâche.",
                    },
                    "output": {
                        "description": "Résultat de la tâche (tout type JSON sérialisable).",
                        "example": {"matrix_row": [1.2, 3.4, 5.6]},
                    },
                },
            },
            "TaskErrorRequest": {
                "type": "object",
                "required": ["node_uuid", "reason"],
                "properties": {
                    "node_uuid": {"type": "string", "example": "node-dell-02"},
                    "reason": {
                        "type": "string",
                        "example": "MemoryError: allocation de 2 Go impossible.",
                        "description": "Description de l'erreur survenue.",
                    },
                },
            },
            # ── Réponses génériques ────────────────────────────────────────
            "ApiSuccessResponse": {
                "type": "object",
                "description": "Réponse standard d'une opération réussie.",
                "properties": {
                    "success": {"type": "boolean", "example": True},
                    "message": {"type": "string", "nullable": True, "example": "Opération réussie."},
                    "data": {
                        "description": "Contenu variable selon l'endpoint (objet, liste, null).",
                        "nullable": True,
                    },
                },
            },
            "ApiErrorResponse": {
                "type": "object",
                "description": "Réponse standard d'une erreur.",
                "properties": {
                    "success": {"type": "boolean", "example": False},
                    "error": {
                        "type": "string",
                        "example": "Identifiants incorrects.",
                        "description": "Message d'erreur humainement lisible.",
                    },
                    "details": {
                        "description": "Détails additionnels (optionnel).",
                        "nullable": True,
                    },
                },
            },
        },
    },
}
