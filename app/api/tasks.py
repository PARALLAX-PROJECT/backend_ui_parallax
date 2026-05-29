"""
Blueprint /api/tasks  (Chercheur)

Routes :
  GET    /              – Liste des programmes de l'utilisateur courant
  POST   /import        – Upload du code source (multipart/form-data)
  GET    /<id>          – Détail d'un programme + progression
  POST   /<id>/submit   – Soumettre pour exécution distribuée
  DELETE /<id>          – Annuler / supprimer un programme
  GET    /<id>/result   – Télécharger l'archive de résultats (ZIP)
  GET    /<id>/logs     – Logs d'exécution
  GET    /<id>/tasks    – Sous-tâches atomiques du programme
"""
import logging
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, current_app, request, send_file
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.extensions import db
from app.models.node import Node, NodeRole, NodeStatus
from app.models.programme import Programme, ProgrammeStatus
from app.models.tache import TacheAtomique
from app.models.user import User
from app.services import storage as storage_svc
from app.services.dispatch import DispatchError, discover_master_ip, send_programme_to_master
from app.services.storage import (
    ArchiveBombError,
    InvalidFileError,
    QuotaExceededError,
    delete_project,
    get_result_archive_path,
    save_project_source,
)
from app.utils.responses import (
    created, error, forbidden, not_found, server_error, success
)

bp = Blueprint("tasks", __name__, url_prefix="/api/tasks")


def _get_programme_or_404(programme_id: str, user_id: str):
    """Retourne le programme si il appartient à l'utilisateur, 404 sinon."""
    prog = Programme.query.filter_by(id=programme_id, owner_id=user_id).first()
    if prog is None:
        return None, not_found("Programme")
    return prog, None


# ──────────────────────────────────────────────
# GET /api/tasks/
# ──────────────────────────────────────────────
@bp.route("/", methods=["GET"])
@jwt_required()
def list_programmes():
    """
    Lister les programmes de l'utilisateur courant.
    ---
    tags:
      - Projets (Chercheur)
    summary: Mes projets
    description: |
      Retourne la liste paginée des programmes soumis par l'utilisateur connecté,
      triés du plus récent au plus ancien. Utilisé pour afficher le tableau de bord
      du chercheur avec l'état d'avancement de chaque projet.
    security:
      - BearerAuth: []
    parameters:
      - in: query
        name: page
        schema:
          type: integer
          default: 1
        description: Numéro de page.
      - in: query
        name: per_page
        schema:
          type: integer
          default: 20
          maximum: 100
        description: Nombre d'éléments par page (max 100).
      - in: query
        name: status
        schema:
          type: string
          enum: [soumis, en_decomposition, en_cours, termine, echec, annule]
        description: Filtrer par statut.
    responses:
      200:
        description: Liste de programmes avec progression.
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
    """
    user_id = get_jwt_identity()
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 20, type=int), 100)
    status_filter = request.args.get("status")

    query = Programme.query.filter_by(owner_id=user_id).order_by(
        Programme.uploaded_at.desc()
    )
    if status_filter:
        query = query.filter_by(status=status_filter)

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    return success(
        data={
            "items": [p.to_dict(include_progress=True) for p in pagination.items],
            "total": pagination.total,
            "page": page,
            "per_page": per_page,
            "pages": pagination.pages,
        }
    )


# ──────────────────────────────────────────────
# POST /api/tasks/import
# ──────────────────────────────────────────────
@bp.route("/import", methods=["POST"])
@jwt_required()
def import_programme():
    """
    Importer le code source d'un projet de calcul.
    ---
    tags:
      - Projets (Chercheur)
    summary: Importer un projet
    description: |
      Upload du code source annoté PARALLAX. Accepte :
      - Un **fichier source unique** : `.py`, `.c`, `.cpp`, `.h`, `.hpp`, `.java`, `.sh`, `.f90`, `.f`, `.r`, `.R`
      - Une **archive** : `.zip`, `.tar`, `.tar.gz`, `.tgz` (extraite et validée)

      **Protections actives :**
      - Taille max fichier : 100 Mo (limite HTTP)
      - Archive décompressée max : 500 Mo (protection zip bomb)
      - Nombre max de fichiers dans l'archive : 1 000
      - Quota utilisateur : 1 Go total (configurable)
      - Prévention path traversal dans les archives

      Le programme créé est dans l'état `soumis`. Il faut ensuite appeler
      `POST /api/tasks/{id}/submit` pour lancer l'exécution.
    security:
      - BearerAuth: []
    requestBody:
      required: true
      content:
        multipart/form-data:
          schema:
            type: object
            required:
              - file
            properties:
              file:
                type: string
                format: binary
                description: Fichier source ou archive à uploader.
              name:
                type: string
                maxLength: 255
                description: Nom du projet (défaut = nom du fichier).
                example: Simulation Monte-Carlo turbulence
              description:
                type: string
                maxLength: 2000
                description: Description optionnelle du projet.
    responses:
      201:
        description: Programme importé avec succès.
        content:
          application/json:
            schema:
              allOf:
                - $ref: '#/components/schemas/ApiSuccessResponse'
              properties:
                data:
                  $ref: '#/components/schemas/ProgrammeResponse'
      400:
        description: Champ `file` manquant ou aucun fichier sélectionné.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
      413:
        description: Quota disque utilisateur dépassé.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
      422:
        description: Extension de fichier non autorisée ou archive corrompue / zip bomb détectée.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
      401:
        description: Token manquant ou invalide.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
    """
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    if user is None:
        return error("Utilisateur introuvable.", status=401)

    if "file" not in request.files:
        return error("Champ 'file' manquant dans la requête multipart.")

    upload = request.files["file"]
    if not upload.filename:
        return error("Aucun fichier sélectionné.")

    name = (request.form.get("name") or upload.filename).strip()[:255]
    description = (request.form.get("description") or "").strip()[:2000]

    # Créer l'entrée en base AVANT de sauvegarder le fichier
    prog = Programme(
        name=name,
        description=description or None,
        owner_id=user_id,
        original_filename=upload.filename,
        status=ProgrammeStatus.SOUMIS.value,
    )
    db.session.add(prog)
    db.session.flush()  # obtenir prog.id sans commit

    try:
        rel_path, size = save_project_source(
            user_id=user_id,
            programme_id=prog.id,
            file_stream=upload.stream,
            filename=upload.filename,
            current_usage_bytes=user.storage_used_bytes,
        )
    except QuotaExceededError as exc:
        db.session.rollback()
        return error(str(exc), status=413)
    except InvalidFileError as exc:
        db.session.rollback()
        return error(str(exc), status=422)
    except ArchiveBombError as exc:
        db.session.rollback()
        return error(str(exc), status=422)
    except Exception as exc:
        db.session.rollback()
        return server_error(f"Erreur lors de la sauvegarde : {exc}")

    prog.source_rel_path = rel_path
    prog.source_size_bytes = size
    user.storage_used_bytes += size
    db.session.commit()

    return created(
        data=prog.to_dict(),
        message="Programme importé avec succès.",
    )


# ──────────────────────────────────────────────
# GET /api/tasks/<id>
# ──────────────────────────────────────────────
@bp.route("/<programme_id>", methods=["GET"])
@jwt_required()
def get_programme(programme_id: str):
    """
    Détail d'un programme avec sa progression.
    ---
    tags:
      - Projets (Chercheur)
    summary: Détail d'un projet
    description: |
      Retourne le détail complet du programme incluant le compteur de sous-tâches
      (total, terminées, échouées, en cours). Utilisé pour la page de suivi
      en temps réel — le frontend peut appeler cet endpoint en polling (ex. toutes les 3 s)
      tant que le statut n'est pas terminal (`termine`, `echec`, `annule`).
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
        description: Détail du programme avec progression.
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
      404:
        description: Programme introuvable ou n'appartient pas à l'utilisateur.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
    """
    user_id = get_jwt_identity()
    prog, err_resp = _get_programme_or_404(programme_id, user_id)
    if err_resp:
        return err_resp
    return success(data=prog.to_dict(include_progress=True))


# ──────────────────────────────────────────────
# POST /api/tasks/<id>/submit
# ──────────────────────────────────────────────
@bp.route("/<programme_id>/submit", methods=["POST"])
@jwt_required()
def submit_programme(programme_id: str):
    """
    Soumettre un programme pour exécution distribuée.
    ---
    tags:
      - Projets (Chercheur)
    summary: Soumettre un projet
    description: |
      Déclenche l'**événement E1** (Soumission de calcul) du tableau 2.11 du rapport.
      Le programme passe de `soumis` à `en_decomposition`.

      L'agent maître prend ensuite en charge :
      1. Lecture et analyse des annotations `@parallax.split`, `@parallax.dag`, `@parallax.shared`
      2. Création des sous-tâches atomiques en base
      3. Distribution aux workers disponibles

      Peut aussi resoumettre un programme en `echec` pour une nouvelle tentative.
    security:
      - BearerAuth: []
    parameters:
      - in: path
        name: programme_id
        required: true
        schema:
          type: string
          format: uuid
        description: UUID du programme à soumettre.
    responses:
      200:
        description: Programme soumis. La décomposition va démarrer.
        content:
          application/json:
            schema:
              allOf:
                - $ref: '#/components/schemas/ApiSuccessResponse'
              properties:
                data:
                  $ref: '#/components/schemas/ProgrammeResponse'
      404:
        description: Programme introuvable.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
      409:
        description: Le programme est dans un état qui ne permet pas la soumission (déjà en cours, terminé, annulé).
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
      401:
        description: Token manquant ou invalide.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
    """
    user_id = get_jwt_identity()
    prog, err_resp = _get_programme_or_404(programme_id, user_id)
    if err_resp:
        return err_resp

    if prog.status not in (ProgrammeStatus.SOUMIS.value, ProgrammeStatus.ECHEC.value):
        return error(
            f"Le programme ne peut pas être soumis dans l'état « {prog.status} ».",
            status=409,
        )
    if not prog.source_rel_path:
        return error("Aucun fichier source associé. Importez d'abord votre code.")

    dispatch_required: bool = current_app.config.get("DISPATCH_REQUIRED", False)
    log = logging.getLogger(__name__)

    # ── 1. Résoudre l'IP du maître ──────────────────────────────────────────
    dispatch_info = _resolve_master_ip()
    dispatch_skipped = False
    dispatch_warning: str | None = None

    if dispatch_info.get("error"):
        if dispatch_required:
            return error(dispatch_info["error"], status=503)
        # Fallback : soumettre sans dispatch (dev / cluster non configuré)
        dispatch_skipped = True
        dispatch_warning = dispatch_info["error"]
        log.warning("Dispatch ignoré (DISPATCH_REQUIRED=false) : %s", dispatch_warning)
        prog.mark_submitted()
        db.session.commit()
        return success(
            data={
                "programme": prog.to_dict(include_progress=True),
                "dispatch": None,
                "dispatch_skipped": True,
                "dispatch_warning": dispatch_warning,
            },
            message=(
                "Programme soumis (sans dispatch cluster). "
                "Lancez les simulateurs et soumettez à nouveau pour tester le dispatch TCP."
            ),
        )

    master_ip: str = dispatch_info["master_ip"]
    controller_ip: str | None = dispatch_info.get("controller_ip")

    # ── 2. Envoyer le programme au maître via TCP ───────────────────────────
    source_dir = Path(current_app.config["STORAGE_ROOT"]) / prog.source_rel_path
    try:
        bytes_sent = send_programme_to_master(
            master_ip=master_ip,
            programme_name=prog.name,
            source_path=source_dir,
        )
    except DispatchError as exc:
        if dispatch_required:
            return error(
                f"Impossible d'envoyer le programme au maître ({master_ip}) : {exc}",
                status=503,
            )
        # Fallback : soumettre malgré l'échec TCP
        log.warning("Envoi TCP échoué, soumission quand même : %s", exc)
        prog.mark_submitted()
        db.session.commit()
        return success(
            data={
                "programme": prog.to_dict(include_progress=True),
                "dispatch": {"master_ip": master_ip, "controller_ip": controller_ip},
                "dispatch_skipped": True,
                "dispatch_warning": str(exc),
            },
            message=f"Programme soumis (maître {master_ip} inaccessible — vérifiez le simulateur).",
        )

    # ── 3. Marquer comme soumis uniquement après envoi réussi ───────────────
    prog.mark_submitted()
    db.session.commit()

    return success(
        data={
            "programme": prog.to_dict(include_progress=True),
            "dispatch": {
                "master_ip": master_ip,
                "controller_ip": controller_ip,
                "bytes_sent": bytes_sent,
            },
            "dispatch_skipped": False,
        },
        message=f"Programme envoyé au maître {master_ip}. Décomposition en cours.",
    )


def _resolve_master_ip() -> dict:
    """
    Détermine l'IP du nœud maître en deux étapes :

    Étape 1 — Via le contrôleur (si enregistré en base) :
      → Envoie un message DISCOVER_MASTER au contrôleur
      ← Reçoit l'IP du maître courant

    Étape 2 — Fallback direct en base :
      → Cherche le nœud avec role=master et status=actif
    """
    # Chercher un contrôleur actif
    controller: Node | None = Node.query.filter_by(
        role=NodeRole.CONTROLLER.value,
        status=NodeStatus.ACTIF.value,
    ).first()

    if controller:
        try:
            master_ip = discover_master_ip(controller.ip)
            return {"master_ip": master_ip, "controller_ip": controller.ip}
        except DispatchError as exc:
            logging.getLogger(__name__).warning(
                "Contrôleur %s inaccessible, fallback DB : %s", controller.ip, exc
            )

    # Fallback : maître en base
    master: Node | None = Node.query.filter_by(
        role=NodeRole.MASTER.value,
        status=NodeStatus.ACTIF.value,
    ).first()

    if master:
        return {"master_ip": master.ip, "controller_ip": None}

    return {
        "error": (
            "Aucun nœud maître disponible. "
            "Vérifiez que le cluster est démarré et qu'un agent maître est enregistré."
        )
    }


# ──────────────────────────────────────────────
# DELETE /api/tasks/<id>
# ──────────────────────────────────────────────
@bp.route("/<programme_id>", methods=["DELETE"])
@jwt_required()
def delete_programme(programme_id: str):
    """
    Supprimer un programme et libérer le stockage associé.
    ---
    tags:
      - Projets (Chercheur)
    summary: Supprimer un projet
    description: |
      Supprime le programme et **tous ses fichiers** (sources + résultats) du disque.
      Si le programme est en cours d'exécution, les sous-tâches actives sont d'abord
      annulées et le quota disque de l'utilisateur est décrémenté.

      **Irréversible.**
    security:
      - BearerAuth: []
    parameters:
      - in: path
        name: programme_id
        required: true
        schema:
          type: string
          format: uuid
        description: UUID du programme à supprimer.
    responses:
      200:
        description: Programme supprimé avec succès.
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
      404:
        description: Programme introuvable.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
    """
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    prog, err_resp = _get_programme_or_404(programme_id, user_id)
    if err_resp:
        return err_resp

    if prog.status == ProgrammeStatus.EN_COURS.value:
        # Annuler les sous-tâches en cours
        from app.models.tache import TacheStatus
        running_tasks = prog.taches.filter(
            TacheAtomique.status.in_([
                TacheStatus.EN_COURS.value,
                TacheStatus.ASSIGNEE.value,
                TacheStatus.EN_ATTENTE.value,
            ])
        ).all()
        for t in running_tasks:
            t.status = TacheStatus.ECHOUEE.value
            t.error_message = "Programme annulé par l'utilisateur."
        prog.mark_cancelled()

    freed = delete_project(user_id, programme_id)
    if user and freed > 0:
        user.storage_used_bytes = max(0, user.storage_used_bytes - freed)

    db.session.delete(prog)
    db.session.commit()

    return success(message="Programme supprimé avec succès.")


# ──────────────────────────────────────────────
# GET /api/tasks/<id>/result
# ──────────────────────────────────────────────
@bp.route("/<programme_id>/result", methods=["GET"])
@jwt_required()
def download_result(programme_id: str):
    """
    Télécharger l'archive ZIP des résultats.
    ---
    tags:
      - Projets (Chercheur)
    summary: Télécharger les résultats
    description: |
      Disponible uniquement quand le programme est dans l'état `termine`.
      Retourne une archive ZIP construite à la demande contenant tous les fichiers
      `.result` générés par les workers.

      Le frontend doit déclencher un téléchargement fichier (ex. via `window.location`
      ou `<a href="..." download>`).
    security:
      - BearerAuth: []
    parameters:
      - in: path
        name: programme_id
        required: true
        schema:
          type: string
          format: uuid
        description: UUID du programme terminé.
    responses:
      200:
        description: Archive ZIP des résultats.
        content:
          application/zip:
            schema:
              type: string
              format: binary
      404:
        description: Programme introuvable ou fichier de résultats absent.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
      409:
        description: Programme pas encore terminé.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
      401:
        description: Token manquant ou invalide.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
    """
    user_id = get_jwt_identity()
    prog, err_resp = _get_programme_or_404(programme_id, user_id)
    if err_resp:
        return err_resp

    if prog.status != ProgrammeStatus.TERMINE.value:
        return error(
            f"Résultats non disponibles. Statut actuel : « {prog.status} ».",
            status=409,
        )

    archive = get_result_archive_path(user_id, programme_id)
    if archive is None or not archive.exists():
        return not_found("Fichier de résultats")

    return send_file(
        str(archive),
        as_attachment=True,
        download_name=f"results_{programme_id[:8]}.zip",
        mimetype="application/zip",
    )


# ──────────────────────────────────────────────
# GET /api/tasks/<id>/logs
# ──────────────────────────────────────────────
@bp.route("/<programme_id>/logs", methods=["GET"])
@jwt_required()
def get_logs(programme_id: str):
    """
    Consulter les logs d'exécution d'un programme.
    ---
    tags:
      - Projets (Chercheur)
    summary: Logs d'exécution
    description: |
      Retourne le journal d'exécution du programme (texte libre écrit par l'agent maître).
      Utile pour diagnostiquer les erreurs de décomposition ou d'exécution.
      Disponible dans tous les états du programme.
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
        description: Journal d'exécution.
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
                      nullable: true
                      example: "[2024-09-15 14:32] Décomposition en 12 sous-tâches. [14:33] 4/12 terminées."
      401:
        description: Token manquant ou invalide.
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
    user_id = get_jwt_identity()
    prog, err_resp = _get_programme_or_404(programme_id, user_id)
    if err_resp:
        return err_resp
    return success(data={"logs": prog.execution_log or ""})


# ──────────────────────────────────────────────
# GET /api/tasks/<id>/tasks
# ──────────────────────────────────────────────
@bp.route("/<programme_id>/tasks", methods=["GET"])
@jwt_required()
def list_subtasks(programme_id: str):
    """
    Lister les sous-tâches atomiques d'un programme.
    ---
    tags:
      - Projets (Chercheur)
    summary: Sous-tâches du projet
    description: |
      Retourne la liste complète des tâches atomiques issues de la décomposition
      du programme par l'agent maître. Permet d'afficher un tableau de progression
      détaillé (quelle fonction, sur quel nœud, combien de tentatives…).
      Triées par ordre de création.
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
        description: Liste des sous-tâches atomiques.
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
      404:
        description: Programme introuvable.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
    """
    user_id = get_jwt_identity()
    prog, err_resp = _get_programme_or_404(programme_id, user_id)
    if err_resp:
        return err_resp

    taches = prog.taches.order_by(TacheAtomique.created_at.asc()).all()
    return success(data=[t.to_dict() for t in taches])
