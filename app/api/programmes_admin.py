"""
Blueprint /api/programmes  (Gestionnaire de cluster)

Vue administrative sur tous les programmes en cours d'exécution.

Routes :
  GET /            – Tous les programmes (filtrables par statut)
  GET /<id>        – Détail d'un programme avec machines impliquées
  POST /<id>/cancel – Annuler un programme en cours
"""
from flask import Blueprint, request
from flask_jwt_extended import jwt_required

from app.extensions import db
from app.models.programme import Programme, ProgrammeStatus
from app.models.tache import TacheAtomique, TacheStatus
from app.utils.decorators import gestionnaire_required
from app.utils.responses import error, not_found, success

bp = Blueprint("programmes_admin", __name__, url_prefix="/api/programmes")


@bp.route("/", methods=["GET"])
@jwt_required()
@gestionnaire_required
def list_all_programmes():
    """
    Lister tous les programmes (toutes origines, vue gestionnaire).
    ---
    tags:
      - Cluster — Programmes (Gestionnaire)
    summary: Tous les programmes
    description: |
      Vue administrative : retourne l'ensemble des programmes soumis par tous
      les chercheurs, triés du plus récent au plus ancien. Permet au gestionnaire
      de surveiller la file de travail globale du cluster.
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
          default: 20
          maximum: 100
      - in: query
        name: status
        schema:
          type: string
          enum: [soumis, en_decomposition, en_cours, termine, echec, annule]
        description: Filtrer par statut.
    responses:
      200:
        description: Liste paginée de tous les programmes.
        content:
          application/json:
            schema:
              allOf:
                - $ref: '#/components/schemas/ApiSuccessResponse'
              properties:
                data:
                  $ref: '#/components/schemas/PaginatedProgrammesResponse'
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
    per_page = min(request.args.get("per_page", 20, type=int), 100)
    status_filter = request.args.get("status")

    query = Programme.query.order_by(Programme.submitted_at.desc())
    if status_filter:
        query = query.filter_by(status=status_filter)

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    return success(data={
        "items": [p.to_dict(include_progress=True) for p in pagination.items],
        "total": pagination.total,
        "page": page,
        "per_page": per_page,
    })


@bp.route("/<programme_id>", methods=["GET"])
@jwt_required()
@gestionnaire_required
def get_programme_admin(programme_id: str):
    """
    Détail administratif d'un programme avec les nœuds impliqués.
    ---
    tags:
      - Cluster — Programmes (Gestionnaire)
    summary: Détail programme (admin)
    description: |
      Retourne le détail complet d'un programme, incluant :
      - La progression des sous-tâches
      - Les UUID des nœuds workers ayant exécuté des tâches (`worker_nodes`)
      - Le journal d'exécution complet (`execution_log`)

      Permet au gestionnaire de retracer l'exécution d'un programme sur le cluster.
    security:
      - BearerAuth: []
    parameters:
      - in: path
        name: programme_id
        required: true
        schema:
          type: string
          format: uuid
        description: UUID du programme.
    responses:
      200:
        description: Détail administratif du programme.
        content:
          application/json:
            schema:
              allOf:
                - $ref: '#/components/schemas/ApiSuccessResponse'
              properties:
                data:
                  allOf:
                    - $ref: '#/components/schemas/ProgrammeResponse'
                  properties:
                    worker_nodes:
                      type: array
                      items:
                        type: string
                      description: UUIDs des nœuds ayant participé à l'exécution.
                      example: ["node-dell-01", "node-dell-02"]
                    execution_log:
                      type: string
                      nullable: true
                      description: Journal d'exécution complet.
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
        description: Programme introuvable.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
    """
    prog = Programme.query.get(programme_id)
    if prog is None:
        return not_found("Programme")

    # Machines impliquées dans l'exécution
    worker_uuids = (
        db.session.query(TacheAtomique.worker_node_uuid)
        .filter(TacheAtomique.programme_id == programme_id)
        .filter(TacheAtomique.worker_node_uuid.isnot(None))
        .distinct()
        .all()
    )
    worker_ids = [r[0] for r in worker_uuids]

    data = prog.to_dict(include_progress=True)
    data["worker_nodes"] = worker_ids
    data["execution_log"] = prog.execution_log
    return success(data=data)


@bp.route("/<programme_id>/tasks", methods=["GET"])
@jwt_required()
@gestionnaire_required
def list_programme_tasks_admin(programme_id: str):
    """
    Lister les sous-tâches atomiques d'un programme (vue gestionnaire).
    ---
    tags:
      - Cluster — Programmes (Gestionnaire)
    summary: Sous-tâches d'un programme (admin)
    description: |
      Retourne toutes les tâches atomiques d'un programme, quelle que soit son origine.
      Contrairement à `GET /api/tasks/{id}/tasks`, cet endpoint n'exige pas
      que le programme appartienne à l'utilisateur courant.
    security:
      - BearerAuth: []
    parameters:
      - in: path
        name: programme_id
        required: true
        schema:
          type: string
          format: uuid
    responses:
      200:
        description: Liste des sous-tâches.
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
        description: Programme introuvable.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
    """
    prog = Programme.query.get(programme_id)
    if prog is None:
        return not_found("Programme")

    taches = prog.taches.order_by(TacheAtomique.created_at.asc()).all()
    return success(data=[t.to_dict() for t in taches])


@bp.route("/<programme_id>/cancel", methods=["POST"])
@jwt_required()
@gestionnaire_required
def cancel_programme(programme_id: str):
    """
    Annuler un programme en cours d'exécution.
    ---
    tags:
      - Cluster — Programmes (Gestionnaire)
    summary: Annuler un programme
    description: |
      Annule toutes les sous-tâches actives (`en_attente`, `assignee`, `en_cours`, `migree`)
      et passe le programme en statut `annule`.

      Utilisé par le gestionnaire pour libérer des ressources cluster en cas
      de programme bloqué, erroné ou prioritaire à remplacer.

      Impossible si le programme est déjà dans un état terminal
      (`termine`, `echec`, `annule`).
    security:
      - BearerAuth: []
    parameters:
      - in: path
        name: programme_id
        required: true
        schema:
          type: string
          format: uuid
        description: UUID du programme à annuler.
    responses:
      200:
        description: Programme annulé avec le nombre de sous-tâches interrompues.
        content:
          application/json:
            schema:
              allOf:
                - $ref: '#/components/schemas/ApiSuccessResponse'
              properties:
                data:
                  $ref: '#/components/schemas/ProgrammeResponse'
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
        description: Programme introuvable.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
      409:
        description: Le programme est déjà dans un état terminal.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
    """
    prog = Programme.query.get(programme_id)
    if prog is None:
        return not_found("Programme")

    if prog.is_terminal:
        return error(
            f"Programme déjà dans un état terminal : {prog.status}.", status=409
        )

    # Annuler toutes les tâches actives
    active_tasks = prog.taches.filter(
        TacheAtomique.status.in_([
            TacheStatus.EN_ATTENTE.value,
            TacheStatus.ASSIGNEE.value,
            TacheStatus.EN_COURS.value,
            TacheStatus.MIGREE.value,
        ])
    ).all()

    for t in active_tasks:
        t.status = TacheStatus.ECHOUEE.value
        t.error_message = "Annulé par le gestionnaire du cluster."

    prog.mark_cancelled()
    db.session.commit()

    return success(
        data=prog.to_dict(),
        message=f"Programme annulé. {len(active_tasks)} sous-tâche(s) interrompue(s).",
    )
