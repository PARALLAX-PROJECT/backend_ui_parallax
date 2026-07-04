"""
Blueprint /api/tasks  (Chercheur)

Routes :
  GET    /              – Liste des programmes de l'utilisateur courant
  POST   /import        – Upload du code source (multipart/form-data)
  GET    /<id>          – Détail d'un programme + progression
  POST   /<id>/submit   – Soumettre pour exécution distribuée
  POST   /<id>/cancel   – Annuler l'exécution d'un programme (sans suppression)
  DELETE /<id>          – Supprimer définitivement un programme
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
from app.models.programme import Programme, ProgrammeStatus
from app.models.tache import TacheAtomique
from app.models.user import User
from app.services import storage as storage_svc
from app.services.predefined_programs import (
    PredefinedProgramError,
    generate_predefined_program,
)
from app.services.dispatch import DispatchError, _read_source_file
from app.services.receptionist_proxy import ReceptionistProxyError, submit_program
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
    submission_mode = (request.form.get("submission_mode") or "source").strip().lower()
    calculation_type = (request.form.get("calculation_type") or "").strip().lower()
    if submission_mode not in {"source", "sum", "matrix"}:
        return error("Mode de soumission invalide.", status=422)
    if submission_mode != "source":
        calculation_type = calculation_type or submission_mode
        if Path(upload.filename).suffix.lower() != ".txt":
            return error("Les calculs guides attendent un fichier .txt.", status=422)

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

    generated_size = 0
    if submission_mode != "source":
        source_dir = Path(current_app.config["STORAGE_ROOT"]) / rel_path
        txt_files = sorted(source_dir.glob("*.txt"))
        if not txt_files:
            db.session.rollback()
            delete_project(user_id, prog.id)
            return error("Fichier .txt introuvable apres sauvegarde.", status=422)
        try:
            _, generated_size = generate_predefined_program(
                calculation_type=calculation_type,
                txt_path=txt_files[0],
                output_dir=source_dir,
            )
        except PredefinedProgramError as exc:
            db.session.rollback()
            delete_project(user_id, prog.id)
            return error(str(exc), status=422)
        except Exception as exc:
            db.session.rollback()
            delete_project(user_id, prog.id)
            return server_error(f"Erreur lors de la generation du programme PARALLAX : {exc}")

    prog.source_rel_path = rel_path
    prog.source_size_bytes = size + generated_size
    user.storage_used_bytes += size + generated_size
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

      Le code source est envoyé tel quel (POST HTTP) au **Receptionist** du cluster
      (`RECEPTIONIST_IP:RECEPTIONIST_HTTP_PORT`), qui le met en file d'attente et le
      relaie au nœud maître dès que celui-ci est connu (voir
      `code_submission_listener_thread` / `forward_code_to_master` dans
      `Receptionnist/reception.c`). L'agent maître prend ensuite en charge :
      1. Lecture et analyse des annotations `@parallax.split`, `@parallax.dag`, `@parallax.shared`
      2. Création des sous-tâches atomiques en base
      3. Distribution aux workers disponibles

      Uniquement supporté pour du C/C++ (seul langage compilé par l'agent maître).

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

    # ── 1. Lire le fichier source principal ─────────────────────────────────
    source_dir = Path(current_app.config["STORAGE_ROOT"]) / prog.source_rel_path
    try:
        code, actual_path = _read_source_file(source_dir, prog.name)
    except DispatchError as exc:
        return error(str(exc), status=422)

    code = _with_prog_name_marker(code, prog.id, actual_path.suffix)
    code = _with_callback_markers(code, actual_path.suffix)

    # ── 2. Envoyer le code au Receptionist du cluster ───────────────────────
    try:
        ack = submit_program(code)
    except ReceptionistProxyError as exc:
        if dispatch_required:
            return error(str(exc), status=503)
        # Fallback : soumettre malgré l'échec (dev / Receptionist non démarré)
        log.warning("Envoi au Receptionist échoué, soumission quand même : %s", exc)
        prog.mark_submitted()
        db.session.commit()
        return success(
            data={
                "programme": prog.to_dict(include_progress=True),
                "dispatch_skipped": True,
                "dispatch_warning": str(exc),
            },
            message="Programme soumis (Receptionist inaccessible — vérifiez le cluster).",
        )

    # ── 3. Marquer comme soumis ──────────────────────────────────────────────
    prog.mark_submitted()
    db.session.commit()

    return success(
        data={
            "programme": prog.to_dict(include_progress=True),
            "dispatch_skipped": False,
            "receptionist_ack": ack,
        },
        message="Programme envoyé au Receptionist du cluster. Décomposition en cours.",
    )


def _with_prog_name_marker(code: bytes, programme_id: str, suffix: str) -> bytes:
    """
    Préfixe le code d'un marqueur `__parallax_prog_name__ = "<uuid>"` si absent,
    pour que le Receptionist nomme le log d'exécution d'après l'UUID du programme
    (voir extract_prog_name dans Receptionnist/reception.c) plutôt que le nom
    choisi par l'utilisateur. Deux utilisateurs peuvent nommer leur programme
    de la même façon ; le Receptionist ne connaît que ce nom et écrase le
    fichier de log précédent portant le même nom (logs/<nom>.c.log) — utiliser
    l'UUID (déjà garanti unique) élimine cette collision. N'a de sens qu'en
    C/C++, seul langage compilé par l'agent maître (gcc, voir
    Execution_Master/utils/master_thread.c).
    """
    if b"__parallax_prog_name__" in code or suffix.lower() not in (".c", ".h", ".cpp", ".hpp"):
        return code
    marker = f'// __parallax_prog_name__ = "{programme_id}"\n'.encode("utf-8")
    return marker + code


def _with_callback_markers(code: bytes, suffix: str) -> bytes:
    """
    Préfixe le code de marqueurs `__parallax_callback_host__` /
    `__parallax_callback_port__` si `BACKEND_CALLBACK_HOST` est configuré.

    Le Receptionist les extrait au moment de la soumission (même mécanisme que
    `__parallax_prog_name__`, voir Receptionnist/reception.c) et les associe au
    nom du programme dans une table en mémoire. Quand le log d'exécution arrive
    (PROG_LOG relayé par le maître via le contrôleur), le Receptionist rappelle
    ce backend en HTTP (`POST /api/cluster/programme-result`) au lieu de se
    contenter d'écrire le log sur disque — c'est ce qui permet de faire passer
    `Programme.status` à `termine` sans que le backend ait besoin de sonder
    `GET /api/nodes/cluster-logs` en boucle.
    """
    if suffix.lower() not in (".c", ".h", ".cpp", ".hpp"):
        return code
    host = current_app.config.get("BACKEND_CALLBACK_HOST")
    if not host:
        return code
    port = current_app.config["BACKEND_CALLBACK_PORT"]
    marker = (
        f'// __parallax_callback_host__ = "{host}"\n'
        f'// __parallax_callback_port__ = "{port}"\n'
    ).encode("utf-8")
    return marker + code


# ──────────────────────────────────────────────
# POST /api/tasks/<id>/cancel
# ──────────────────────────────────────────────
@bp.route("/<programme_id>/cancel", methods=["POST"])
@jwt_required()
def cancel_own_programme(programme_id: str):
    """
    Annuler un programme en cours (sans le supprimer).
    ---
    tags:
      - Projets (Chercheur)
    summary: Annuler l'exécution
    description: |
      Annule toutes les sous-tâches actives et passe le programme en statut `annule`.
      Le programme reste accessible (logs, métadonnées). Utilisez DELETE pour le supprimer.
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
        description: Programme annulé.
      404:
        description: Programme introuvable.
      409:
        description: Le programme est déjà dans un état terminal.
    """
    user_id = get_jwt_identity()
    prog, err_resp = _get_programme_or_404(programme_id, user_id)
    if err_resp:
        return err_resp

    if prog.is_terminal:
        return error(
            f"Programme déjà dans un état terminal : {prog.status}.", status=409
        )

    active_tasks = prog.taches.filter(
        TacheAtomique.status.in_([
            "en_attente", "assignee", "en_cours", "migree",
        ])
    ).all()

    for t in active_tasks:
        t.status = "echouee"
        t.error_message = "Annulé par le chercheur."

    prog.mark_cancelled()
    db.session.commit()

    return success(
        data=prog.to_dict(include_progress=True),
        message=f"Programme annulé. {len(active_tasks)} sous-tâche(s) interrompue(s).",
    )


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
