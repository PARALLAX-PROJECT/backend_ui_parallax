"""
Blueprint /api/nodes  (Gestionnaire de cluster)

Routes :
  GET    /          – Liste tous les noeuds du cluster
  GET    /<uuid>    – Détail d'un noeud (profil + métriques courantes)
  DELETE /<uuid>    – Retire un noeud du cluster
  GET    /<uuid>/tasks – Sous-tâches assignées à ce noeud
  GET    /stats     – Statistiques globales du cluster
"""
from flask import Blueprint, request
from flask_jwt_extended import jwt_required

from app.extensions import db
from app.models.node import Node, NodeStatus
from app.models.tache import TacheAtomique, TacheStatus
from app.utils.decorators import gestionnaire_required
from app.utils.responses import error, not_found, success

bp = Blueprint("nodes", __name__, url_prefix="/api/nodes")


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
    total = Node.query.count()
    actifs = Node.query.filter_by(status=NodeStatus.ACTIF.value).count()
    surcharges = Node.query.filter_by(status=NodeStatus.SURCHARGE.value).count()
    en_panne = Node.query.filter_by(status=NodeStatus.EN_PANNE.value).count()
    maintenance = Node.query.filter_by(status=NodeStatus.EN_MAINTENANCE.value).count()

    tasks_running = TacheAtomique.query.filter_by(status=TacheStatus.EN_COURS.value).count()
    tasks_waiting = TacheAtomique.query.filter_by(status=TacheStatus.EN_ATTENTE.value).count()

    return success(data={
        "nodes": {
            "total": total,
            "actifs": actifs,
            "surcharges": surcharges,
            "en_panne": en_panne,
            "en_maintenance": maintenance,
        },
        "tasks": {
            "en_cours": tasks_running,
            "en_attente": tasks_waiting,
        },
    })


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

    query = Node.query.order_by(Node.registered_at.desc())
    if status_filter:
        query = query.filter_by(status=status_filter)
    if role_filter:
        query = query.filter_by(role=role_filter)

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    return success(data={
        "items": [n.to_dict(include_profile=True) for n in pagination.items],
        "total": pagination.total,
        "page": page,
        "per_page": per_page,
        "pages": pagination.pages,
    })


# ──────────────────────────────────────────────
# GET /api/nodes/<uuid>
# ──────────────────────────────────────────────
@bp.route("/<node_uuid>", methods=["GET"])
@jwt_required()
@gestionnaire_required
def get_node(node_uuid: str):
    """
    Détail d'un nœud avec son profil et ses heartbeats récents.
    ---
    tags:
      - Cluster — Noeuds (Gestionnaire)
    summary: Détail d'un nœud
    description: |
      Retourne le profil complet du nœud (capacités matérielles, statut actuel,
      métriques temps réel) et les 10 derniers heartbeats reçus. Utile pour
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
        description: Détail du nœud avec heartbeats récents.
        content:
          application/json:
            schema:
              allOf:
                - $ref: '#/components/schemas/ApiSuccessResponse'
              properties:
                data:
                  allOf:
                    - $ref: '#/components/schemas/NodeResponse'
                  properties:
                    recent_heartbeats:
                      type: array
                      items:
                        $ref: '#/components/schemas/HeartbeatRecord'
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
    node = Node.query.get(node_uuid)
    if node is None:
        return not_found("Noeud")

    # Derniers heartbeats (10 plus récents)
    recent_hb = (
        node.heartbeats.order_by(db.desc("received_at")).limit(10).all()
    )
    data = node.to_dict(include_profile=True)
    data["recent_heartbeats"] = [hb.to_dict() for hb in recent_hb]
    return success(data=data)


# ──────────────────────────────────────────────
# DELETE /api/nodes/<uuid>
# ──────────────────────────────────────────────
@bp.route("/<node_uuid>", methods=["DELETE"])
@jwt_required()
@gestionnaire_required
def remove_node(node_uuid: str):
    """
    Retirer définitivement un nœud du cluster.
    ---
    tags:
      - Cluster — Noeuds (Gestionnaire)
    summary: Retirer un nœud
    description: |
      Passe le nœud en statut `eteint`. Si des tâches sont assignées à ce nœud,
      elles sont marquées `migree` pour réassignation automatique par l'agent maître.

      **Irréversible** : le nœud devra se ré-enregistrer via
      `POST /api/cluster/register` pour réintégrer le cluster.
    security:
      - BearerAuth: []
    parameters:
      - in: path
        name: node_uuid
        required: true
        schema:
          type: string
        description: UUID du nœud à retirer.
    responses:
      200:
        description: Nœud retiré et tâches migrées.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiSuccessResponse'
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
    node = Node.query.get(node_uuid)
    if node is None:
        return not_found("Noeud")

    # Migrer les sous-tâches en cours sur ce noeud
    running = TacheAtomique.query.filter_by(
        worker_node_uuid=node_uuid,
    ).filter(
        TacheAtomique.status.in_([
            TacheStatus.ASSIGNEE.value,
            TacheStatus.EN_COURS.value,
        ])
    ).all()

    for task in running:
        task.mark_migrated()

    node.status = NodeStatus.ETEINT.value
    db.session.commit()

    return success(
        message=f"Noeud {node_uuid[:8]} retiré du cluster. "
                f"{len(running)} tâche(s) migrée(s)."
    )


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
      Retourne les sous-tâches atomiques assignées à ce nœud (100 max, les plus récentes).
      Utile pour diagnostiquer un nœud surchargé ou voir sa charge de travail actuelle.
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
      404:
        description: Nœud introuvable.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
    """
    node = Node.query.get(node_uuid)
    if node is None:
        return not_found("Noeud")

    status_filter = request.args.get("status")
    query = node.assigned_tasks
    if status_filter:
        query = query.filter_by(status=status_filter)

    tasks = query.order_by(TacheAtomique.created_at.desc()).limit(100).all()
    return success(data=[t.to_dict() for t in tasks])


# ──────────────────────────────────────────────
# GET /api/nodes/<uuid>/heartbeats
# ──────────────────────────────────────────────
@bp.route("/<node_uuid>/heartbeats", methods=["GET"])
@jwt_required()
@gestionnaire_required
def node_heartbeats(node_uuid: str):
    """
    Historique des heartbeats d'un nœud.
    ---
    tags:
      - Cluster — Noeuds (Gestionnaire)
    summary: Historique heartbeats
    description: |
      Retourne l'historique des heartbeats reçus pour ce nœud (du plus récent au plus ancien).
      Permet de tracer des courbes d'utilisation CPU/RAM dans le temps et détecter
      les périodes de surcharge ou d'indisponibilité.
    security:
      - BearerAuth: []
    parameters:
      - in: path
        name: node_uuid
        required: true
        schema:
          type: string
      - in: query
        name: limit
        schema:
          type: integer
          default: 50
          maximum: 500
        description: Nombre de heartbeats à retourner (max 500).
    responses:
      200:
        description: Historique des heartbeats.
        content:
          application/json:
            schema:
              allOf:
                - $ref: '#/components/schemas/ApiSuccessResponse'
              properties:
                data:
                  type: array
                  items:
                    $ref: '#/components/schemas/HeartbeatRecord'
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
    node = Node.query.get(node_uuid)
    if node is None:
        return not_found("Noeud")

    limit = min(request.args.get("limit", 50, type=int), 500)
    hbs = node.heartbeats.order_by(db.desc("received_at")).limit(limit).all()
    return success(data=[hb.to_dict() for hb in hbs])


# ──────────────────────────────────────────────
# PATCH /api/nodes/<uuid>/maintenance
# ──────────────────────────────────────────────
@bp.route("/<node_uuid>/maintenance", methods=["PATCH"])
@jwt_required()
@gestionnaire_required
def toggle_maintenance(node_uuid: str):
    """
    Activer ou désactiver le mode maintenance d'un nœud.
    ---
    tags:
      - Cluster — Noeuds (Gestionnaire)
    summary: Mode maintenance
    description: |
      **Activer** (`enable: true`) : le nœud passe en `en_maintenance`, ses tâches actives
      sont migrées vers d'autres nœuds. Le nœud ne recevra plus de nouvelles tâches.

      **Désactiver** (`enable: false`) : le nœud repasse en `actif` et redevient
      éligible pour recevoir des tâches.

      Utile pour planifier des interventions matérielles sans perturber les calculs en cours.
    security:
      - BearerAuth: []
    parameters:
      - in: path
        name: node_uuid
        required: true
        schema:
          type: string
        description: UUID du nœud.
    requestBody:
      required: false
      content:
        application/json:
          schema:
            $ref: '#/components/schemas/MaintenanceRequest'
    responses:
      200:
        description: Statut du nœud mis à jour.
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
      409:
        description: Le nœud est déjà dans l'état demandé.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
    """
    node = Node.query.get(node_uuid)
    if node is None:
        return not_found("Noeud")

    data = request.get_json(silent=True) or {}
    enable = data.get("enable", True)

    if enable:
        if node.status == NodeStatus.EN_MAINTENANCE.value:
            return error("Le noeud est déjà en maintenance.", status=409)
        # Migrer les tâches actives
        running = TacheAtomique.query.filter_by(
            worker_node_uuid=node_uuid,
        ).filter(
            TacheAtomique.status.in_([
                TacheStatus.ASSIGNEE.value, TacheStatus.EN_COURS.value
            ])
        ).all()
        for t in running:
            t.mark_migrated()
        node.status = NodeStatus.EN_MAINTENANCE.value
        msg = f"Noeud {node_uuid[:8]} mis en maintenance. {len(running)} tâche(s) migrée(s)."
    else:
        if node.status != NodeStatus.EN_MAINTENANCE.value:
            return error("Le noeud n'est pas en maintenance.", status=409)
        node.status = NodeStatus.ACTIF.value
        msg = f"Noeud {node_uuid[:8]} réactivé."

    db.session.commit()
    return success(data=node.to_dict(), message=msg)
