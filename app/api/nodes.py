"""
Blueprint /api/nodes  (Gestionnaire de cluster)

Routes :
  GET    /             – Liste live des noeuds du cluster (proxy Receptionist)
  GET    /stats        – Statistiques globales du cluster (proxy Receptionist)
  GET    /cluster-logs        – Liste des logs de programmes terminés (proxy Receptionist)
  GET    /cluster-logs/<name> – Contenu d'un log de programme (proxy Receptionist)
  GET    /<uuid>       – Détail live d'un noeud (proxy Receptionist)
  GET    /<uuid>/logs  – Log d'exécution d'un noeud (proxy Receptionist)
  GET    /<uuid>/tasks – Sous-tâches assignées à ce noeud
  GET    /master       – Nœud maître + contrôleur actifs (proxy Receptionist)

Tout ce blueprint lit en direct le serveur HTTP du Receptionist (port 9010),
qui lit lui-même la couche de gossip du contrôleur — voir
app/services/receptionist_proxy.py. Rien n'est lu depuis la table `nodes` en
base : les agents C ne s'enregistrent jamais via /api/cluster/register (aucun
appel HTTP de leur côté), donc cette table reste vide en pratique.

Pas d'actions d'écriture (retrait de nœud, mode maintenance, historique des
heartbeats) : elles n'ont pas d'équivalent côté cluster réel aujourd'hui — ni
mécanisme C pour décommissionner/mettre un nœud en maintenance à distance, ni
historique de heartbeats en dehors de la table `nodes` (jamais peuplée). Les
avoir gardées comme actions no-op sur cette table aurait juste 404 à chaque
appel réel ; mieux vaut les réintroduire une fois qu'un mécanisme cluster
réel existe pour les porter.
"""
from flask import Blueprint, request
from flask_jwt_extended import jwt_required

from app.models.programme import Programme, ProgrammeStatus
from app.models.tache import TacheAtomique, TacheStatus
from app.services.receptionist_proxy import (
    ClusterLogNotFoundError,
    NodeLogNotFoundError,
    ReceptionistProxyError,
    fetch_cluster_log_content,
    fetch_cluster_logs,
    fetch_live_nodes,
    fetch_node_log,
)
from app.services.runtime_settings import get_receptionist_config, set_receptionist_config
from app.utils.decorators import gestionnaire_required
from app.utils.responses import error, not_found, success

bp = Blueprint("nodes", __name__, url_prefix="/api/nodes")


def _live_node_to_dict(n: dict) -> dict:
    """Normalise un noeud renvoyé par le Receptionist vers le format API habituel."""
    return {
        "uuid": n.get("uuid"),
        "ip": n.get("ip"),
        "port": n.get("port"),
        "hostname": None,
        "role": n.get("role"),
        "status": n.get("status"),
        "metrics": {
            "cpu_usage": n.get("cpu"),
            "ram_usage": n.get("ram"),
            "score": n.get("score"),
            "ram_available_mb": n.get("ram_available_mb"),
            "disk_usage": n.get("disk_usage"),
        },
        "profile": {
            "cpu_cores": n.get("cores"),
            "cpu_model": n.get("model"),
            "cpu_threads_per_core": n.get("threads_per_core"),
            "cpu_freq_mhz": n.get("freq_mhz"),
            "ram_total_mb": n.get("ram_total_mb"),
            "disk_total_mb": n.get("disk_total_mb"),
            "network_iface": n.get("network_iface"),
        },
    }


# ──────────────────────────────────────────────
# GET /api/nodes/stats
# ──────────────────────────────────────────────
@bp.route("/stats", methods=["GET"])
@jwt_required()
@gestionnaire_required
def cluster_stats():
    """
    Statistiques globales du cluster (tableau de bord gestionnaire).
    ---
    tags:
      - Cluster — Noeuds (Gestionnaire)
    summary: Statistiques du cluster
    description: |
      Retourne une vue consolidée de l'état du cluster : répartition des nœuds
      par statut et nombre de tâches en cours / en attente. Point d'entrée principal
      du tableau de bord gestionnaire, à rafraîchir toutes les 5–10 secondes.
    security:
      - BearerAuth: []
    responses:
      200:
        description: Statistiques du cluster.
        content:
          application/json:
            schema:
              allOf:
                - $ref: '#/components/schemas/ApiSuccessResponse'
              properties:
                data:
                  $ref: '#/components/schemas/ClusterStatsResponse'
      401:
        description: Token manquant ou invalide.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
      403:
        description: Rôle gestionnaire requis.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
    """
    try:
        live_nodes = fetch_live_nodes()
    except ReceptionistProxyError as exc:
        return error(str(exc), status=503)

    actifs = sum(1 for n in live_nodes if n.get("status") in ("active", "suspect"))
    surcharges = sum(1 for n in live_nodes if n.get("status") == "overloaded")
    en_panne = sum(1 for n in live_nodes if n.get("status") == "failed")
    maintenance = sum(1 for n in live_nodes if n.get("status") == "maintenance")

    tasks_running = TacheAtomique.query.filter_by(status=TacheStatus.EN_COURS.value).count()
    tasks_waiting = TacheAtomique.query.filter_by(status=TacheStatus.EN_ATTENTE.value).count()
    programmes_running = Programme.query.filter_by(status=ProgrammeStatus.EN_COURS.value).count()

    return success(data={
        "nodes": {
            "total": len(live_nodes),
            "actifs": actifs,
            "surcharges": surcharges,
            "en_panne": en_panne,
            "en_maintenance": maintenance,
        },
        "tasks": {
            "en_cours": tasks_running,
            "en_attente": tasks_waiting,
        },
        "programmes": {
            "en_cours": programmes_running,
        },
    })


# ──────────────────────────────────────────────
# GET/PUT /api/nodes/receptionist-config
# ──────────────────────────────────────────────
@bp.route("/receptionist-config", methods=["GET"])
@jwt_required()
@gestionnaire_required
def read_receptionist_config():
    """
    Adresse actuelle du Receptionist utilisée par ce backend.
    ---
    tags:
      - Cluster — Noeuds (Gestionnaire)
    summary: Lire l'IP/port du Receptionist
    security:
      - BearerAuth: []
    responses:
      200:
        description: Configuration courante.
    """
    return success(data=get_receptionist_config())


@bp.route("/receptionist-config", methods=["PUT"])
@jwt_required()
@gestionnaire_required
def update_receptionist_config():
    """
    Change l'IP/port du Receptionist utilisée par ce backend, sans redémarrage.
    ---
    tags:
      - Cluster — Noeuds (Gestionnaire)
    summary: Mettre à jour l'IP/port du Receptionist
    description: |
      Persisté dans instance/runtime_settings.json et appliqué immédiatement
      à ce process — utile quand la machine hébergeant le Receptionist change
      d'adresse (DHCP) en cours de session, sans avoir à éditer le .env ni
      relancer le backend.
    security:
      - BearerAuth: []
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required: [ip, port]
            properties:
              ip: { type: string, example: "192.168.30.43" }
              port: { type: integer, example: 9010 }
    responses:
      200:
        description: Configuration mise à jour.
      400:
        description: IP ou port invalide.
    """
    body = request.get_json(silent=True) or {}
    ip = (body.get("ip") or "").strip()
    port = body.get("port")

    if not ip:
        return error("L'adresse IP est requise.", status=400)
    try:
        port = int(port)
    except (TypeError, ValueError):
        return error("Le port doit être un entier.", status=400)
    if not (1 <= port <= 65535):
        return error("Le port doit être compris entre 1 et 65535.", status=400)

    updated = set_receptionist_config(ip, port)
    return success(data=updated, message="Configuration du Receptionist mise à jour.")


# ──────────────────────────────────────────────
# GET /api/nodes/cluster-logs
# ──────────────────────────────────────────────
@bp.route("/cluster-logs", methods=["GET"])
@jwt_required()
@gestionnaire_required
def list_cluster_logs():
    """
    Lister les logs de programmes terminés, tels que reçus par le Receptionist.
    ---
    tags:
      - Cluster — Noeuds (Gestionnaire)
    summary: Logs de programmes (cluster)
    description: |
      Interroge en direct `GET /logs` sur le Receptionist (port 9010). Ces fichiers
      sont écrits par le Receptionist quand le maître relaie, via le contrôleur,
      le log complet d'un programme terminé (`PROG_LOG_TYPE`). Distinct de
      `GET /api/tasks/<id>/logs`, qui lit `Programme.execution_log` en base.
    security:
      - BearerAuth: []
    responses:
      200:
        description: Liste des fichiers de log disponibles.
        content:
          application/json:
            schema:
              allOf:
                - $ref: '#/components/schemas/ApiSuccessResponse'
              properties:
                data:
                  type: array
                  items:
                    type: object
                    properties:
                      name:
                        type: string
                      size:
                        type: integer
      401:
        description: Token manquant ou invalide.
      403:
        description: Rôle gestionnaire requis.
      503:
        description: Receptionist injoignable.
    """
    try:
        logs = fetch_cluster_logs()
    except ReceptionistProxyError as exc:
        return error(str(exc), status=503)

    return success(data=logs)


# ──────────────────────────────────────────────
# GET /api/nodes/cluster-logs/<name>
# ──────────────────────────────────────────────
@bp.route("/cluster-logs/<path:log_name>", methods=["GET"])
@jwt_required()
@gestionnaire_required
def get_cluster_log(log_name: str):
    """
    Contenu d'un log de programme, lu en direct depuis le Receptionist.
    ---
    tags:
      - Cluster — Noeuds (Gestionnaire)
    summary: Contenu d'un log de programme
    description: |
      Interroge en direct `GET /logs/<name>` sur le Receptionist (port 9010).
      Le nom doit correspondre à l'une des entrées renvoyées par
      `GET /api/nodes/cluster-logs`.
    security:
      - BearerAuth: []
    parameters:
      - in: path
        name: log_name
        required: true
        schema:
          type: string
        description: Nom du fichier de log (ex. `submitted_prog.c.log`).
    responses:
      200:
        description: Contenu du log.
        content:
          application/json:
            schema:
              allOf:
                - $ref: '#/components/schemas/ApiSuccessResponse'
              properties:
                data:
                  type: object
                  properties:
                    logs:
                      type: string
      401:
        description: Token manquant ou invalide.
      403:
        description: Rôle gestionnaire requis.
      404:
        description: Fichier de log introuvable.
      503:
        description: Receptionist injoignable.
    """
    try:
        content = fetch_cluster_log_content(log_name)
    except ClusterLogNotFoundError:
        return not_found("Log de programme")
    except ReceptionistProxyError as exc:
        return error(str(exc), status=503)

    return success(data={"logs": content})


# ──────────────────────────────────────────────
# GET /api/nodes/
# ──────────────────────────────────────────────
@bp.route("/", methods=["GET"])
@jwt_required()
@gestionnaire_required
def list_nodes():
    """
    Lister tous les nœuds du cluster.
    ---
    tags:
      - Cluster — Noeuds (Gestionnaire)
    summary: Liste des nœuds
    description: |
      Retourne la liste paginée de tous les nœuds enregistrés dans le cluster,
      avec leur profil matériel et métriques courantes. Permet au gestionnaire
      de visualiser la composition du cluster et l'état de chaque machine.
    security:
      - BearerAuth: []
    parameters:
      - in: query
        name: page
        schema:
          type: integer
          default: 1
      - in: query
        name: per_page
        schema:
          type: integer
          default: 50
          maximum: 200
      - in: query
        name: status
        schema:
          type: string
          enum: [actif, surcharge, en_panne, en_maintenance, eteint]
        description: Filtrer par statut.
      - in: query
        name: role
        schema:
          type: string
          enum: [master, worker, controller, remplacant]
        description: Filtrer par rôle.
    responses:
      200:
        description: Liste paginée des nœuds.
        content:
          application/json:
            schema:
              allOf:
                - $ref: '#/components/schemas/ApiSuccessResponse'
              properties:
                data:
                  $ref: '#/components/schemas/PaginatedNodesResponse'
      401:
        description: Token manquant ou invalide.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
      403:
        description: Rôle gestionnaire requis.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
    """
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 50, type=int), 200)
    status_filter = request.args.get("status")
    role_filter = request.args.get("role")

    try:
        live_nodes = fetch_live_nodes()
    except ReceptionistProxyError as exc:
        return error(str(exc), status=503)

    if status_filter:
        live_nodes = [n for n in live_nodes if n.get("status") == status_filter]
    if role_filter:
        live_nodes = [n for n in live_nodes if n.get("role") == role_filter]

    total = len(live_nodes)
    start = (page - 1) * per_page
    page_items = live_nodes[start:start + per_page]
    pages = max(1, (total + per_page - 1) // per_page)

    return success(data={
        "items": [_live_node_to_dict(n) for n in page_items],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    })


# ──────────────────────────────────────────────
# GET /api/nodes/<uuid>
# ──────────────────────────────────────────────
@bp.route("/<node_uuid>", methods=["GET"])
@jwt_required()
@gestionnaire_required
def get_node(node_uuid: str):
    """
    Détail d'un nœud (profil matériel + métriques courantes).
    ---
    tags:
      - Cluster — Noeuds (Gestionnaire)
    summary: Détail d'un nœud
    description: |
      Retourne le profil complet du nœud (capacités matérielles, statut actuel,
      métriques temps réel), lu en direct depuis le Receptionist. Utile pour
      la page de détail d'un nœud dans le tableau de bord gestionnaire.
    security:
      - BearerAuth: []
    parameters:
      - in: path
        name: node_uuid
        required: true
        schema:
          type: string
        description: UUID du nœud.
        example: node-dell-01
    responses:
      200:
        description: Détail du nœud.
        content:
          application/json:
            schema:
              allOf:
                - $ref: '#/components/schemas/ApiSuccessResponse'
              properties:
                data:
                  $ref: '#/components/schemas/NodeResponse'
      401:
        description: Token manquant ou invalide.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
      403:
        description: Rôle gestionnaire requis.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
      404:
        description: Nœud introuvable.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
    """
    try:
        live_nodes = fetch_live_nodes()
    except ReceptionistProxyError as exc:
        return error(str(exc), status=503)

    node = next((n for n in live_nodes if n.get("uuid") == node_uuid), None)
    if node is None:
        return not_found("Noeud")

    return success(data=_live_node_to_dict(node))


# ──────────────────────────────────────────────
# GET /api/nodes/<uuid>/logs
# ──────────────────────────────────────────────
@bp.route("/<node_uuid>/logs", methods=["GET"])
@jwt_required()
@gestionnaire_required
def node_logs(node_uuid: str):
    """
    Log d'exécution d'un nœud, lu en direct depuis le Receptionist du cluster.
    ---
    tags:
      - Cluster — Noeuds (Gestionnaire)
    summary: Logs d'un nœud
    description: |
      Interroge en direct le Receptionist (`GET /node-logs/<uuid>` sur son port HTTP,
      9010 par défaut), qui relaie la demande au contrôleur via la couche de gossip.
      Aucune donnée n'est mise en cache côté backend Flask.
    security:
      - BearerAuth: []
    parameters:
      - in: path
        name: node_uuid
        required: true
        schema:
          type: string
        description: UUID du nœud.
    responses:
      200:
        description: Contenu du log du nœud.
        content:
          application/json:
            schema:
              allOf:
                - $ref: '#/components/schemas/ApiSuccessResponse'
              properties:
                data:
                  type: object
                  properties:
                    logs:
                      type: string
      401:
        description: Token manquant ou invalide.
      403:
        description: Rôle gestionnaire requis.
      404:
        description: Aucun log disponible pour ce noeud.
      503:
        description: Receptionist ou contrôleur du cluster injoignable.
    """
    try:
        logs = fetch_node_log(node_uuid)
    except NodeLogNotFoundError:
        return not_found("Log du noeud")
    except ReceptionistProxyError as exc:
        return error(str(exc), status=503)

    return success(data={"logs": logs})


# ──────────────────────────────────────────────
# DELETE /api/nodes/<uuid>
# ──────────────────────────────────────────────
# ──────────────────────────────────────────────
# GET /api/nodes/<uuid>/tasks
# ──────────────────────────────────────────────
@bp.route("/<node_uuid>/tasks", methods=["GET"])
@jwt_required()
@gestionnaire_required
def node_tasks(node_uuid: str):
    """
    Lister les tâches assignées à un nœud.
    ---
    tags:
      - Cluster — Noeuds (Gestionnaire)
    summary: Tâches d'un nœud
    description: |
      Retourne les sous-tâches atomiques assignées à ce nœud (100 max, les plus récentes),
      identifié par son UUID live (voir `GET /api/nodes/`). Ne dépend pas de la table
      `nodes` en base — les sous-tâches référencent l'UUID du nœud directement.
    security:
      - BearerAuth: []
    parameters:
      - in: path
        name: node_uuid
        required: true
        schema:
          type: string
      - in: query
        name: status
        schema:
          type: string
          enum: [en_attente, assignee, en_cours, terminee, echouee, migree]
        description: Filtrer par statut de tâche.
    responses:
      200:
        description: Liste des sous-tâches du nœud.
        content:
          application/json:
            schema:
              allOf:
                - $ref: '#/components/schemas/ApiSuccessResponse'
              properties:
                data:
                  type: array
                  items:
                    $ref: '#/components/schemas/TacheAtomiqueResponse'
      401:
        description: Token manquant ou invalide.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
      403:
        description: Rôle gestionnaire requis.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
    """
    status_filter = request.args.get("status")
    query = TacheAtomique.query.filter_by(worker_node_uuid=node_uuid)
    if status_filter:
        query = query.filter_by(status=status_filter)

    tasks = query.order_by(TacheAtomique.created_at.desc()).limit(100).all()
    return success(data=[t.to_dict() for t in tasks])


# ──────────────────────────────────────────────
# GET /api/nodes/master
# ──────────────────────────────────────────────
@bp.route("/master", methods=["GET"])
@jwt_required()
def get_master():
    """
    Retourne les informations du nœud maître actif, lues en direct depuis le Receptionist.
    Accessible à tous les utilisateurs authentifiés (chercheurs, étudiants, gestionnaires).
    Utilisé par le frontend pour afficher le maître courant avant soumission.
    """
    from flask import current_app

    config_controller_ip = current_app.config.get("CONTROLLER_IP")
    config_master_ip = current_app.config.get("MASTER_NODE_IP")

    try:
        live_nodes = fetch_live_nodes()
    except ReceptionistProxyError as exc:
        return success(
            data={
                "master": None,
                "controller": None,
                "cluster_ready": False,
                "config_controller_ip": config_controller_ip,
                "config_master_ip": config_master_ip,
            },
            message=f"Cluster injoignable : {exc}",
        )

    master = next((n for n in live_nodes if n.get("role") == "master"), None)
    controller = next((n for n in live_nodes if n.get("role") == "controller"), None)

    if master is None:
        return success(
            data={
                "master": None,
                "controller": _live_node_to_dict(controller) if controller else None,
                "cluster_ready": False,
                "config_controller_ip": config_controller_ip,
                "config_master_ip": config_master_ip,
            },
            message="Aucun nœud maître actif dans le cluster actuellement.",
        )

    return success(
        data={
            "master": _live_node_to_dict(master),
            "controller": _live_node_to_dict(controller) if controller else None,
            "cluster_ready": True,
            "config_controller_ip": config_controller_ip,
            "config_master_ip": config_master_ip,
        },
        message="Nœud maître trouvé.",
    )
