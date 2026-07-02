"""
Blueprint /api/cluster  (API interne noeud → maître)

Toutes les routes de ce blueprint requièrent l'en-tête X-Cluster-Key.

Routes :
  POST /register            – Enregistrement d'un nouveau noeud
  POST /heartbeat           – Réception d'un heartbeat
  POST /tasks/<id>/accept   – Worker accepte une tâche
  POST /tasks/<id>/result   – Worker retourne son résultat
  POST /tasks/<id>/error    – Worker signale une erreur
  GET  /tasks/next          – Worker demande la prochaine tâche disponible
  POST /programme-result    – Callback du Receptionist : résultat d'un programme soumis
"""
import json
from datetime import datetime, timezone

from flask import Blueprint, request

from app.extensions import db
from app.models.node import Heartbeat, Node, NodeProfile, NodeRole, NodeStatus
from app.models.programme import Programme, ProgrammeStatus
from app.models.tache import TacheAtomique, TacheStatus
from app.utils.decorators import cluster_internal
from app.utils.responses import created, error, not_found, success

bp = Blueprint("cluster", __name__, url_prefix="/api/cluster")


# ──────────────────────────────────────────────
# POST /api/cluster/register
# ──────────────────────────────────────────────
@bp.route("/register", methods=["POST"])
@cluster_internal
def register_node():
    """
    Enregistrer ou mettre à jour un nœud dans le cluster.
    ---
    tags:
      - Cluster — API Interne
    summary: Enregistrement d'un nœud
    description: |
      **Algorithme 1 du rapport PARALLAX.**

      Appelé par l'agent C d'un nœud au démarrage (ou redémarrage).
      Si le nœud existe déjà (même UUID), ses métriques et son profil sont mis à jour.
      Sinon, un nouveau nœud est créé avec le statut `actif`.

      Requiert l'en-tête `X-Cluster-Key`.
    security:
      - ClusterKey: []
    requestBody:
      required: true
      content:
        application/json:
          schema:
            $ref: '#/components/schemas/NodeRegisterRequest'
    responses:
      201:
        description: Nœud enregistré (ou mis à jour) avec succès.
        content:
          application/json:
            schema:
              allOf:
                - $ref: '#/components/schemas/ApiSuccessResponse'
              properties:
                data:
                  $ref: '#/components/schemas/NodeResponse'
      400:
        description: Champs `uuid` ou `ip` manquants.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
      401:
        description: Clé cluster invalide ou absente.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
    """
    data = request.get_json(silent=True) or {}
    node_uuid = (data.get("uuid") or "").strip()
    ip = (data.get("ip") or "").strip()

    if not node_uuid or not ip:
        return error("Champs 'uuid' et 'ip' requis.")

    # Upsert : si le noeud se ré-enregistre (redémarrage), on met à jour
    node = Node.query.get(node_uuid)
    if node is None:
        node = Node(uuid=node_uuid, ip=ip)
        db.session.add(node)
    else:
        node.ip = ip
        node.status = NodeStatus.ACTIF.value

    node.hostname = data.get("hostname")
    node.role = (data.get("role") or NodeRole.WORKER.value).strip()
    node.last_heartbeat_at = datetime.now(timezone.utc)

    # Profil matériel
    profile_data = data.get("profile") or {}
    if profile_data:
        if node.profile is None:
            node.profile = NodeProfile(node_uuid=node_uuid)
        prof = node.profile
        prof.cpu_cores = int(profile_data.get("cpu_cores", 1))
        prof.cpu_freq_mhz = float(profile_data.get("cpu_freq_mhz", 0))
        prof.arch_cpu = str(profile_data.get("arch_cpu", "x86_64"))
        prof.ram_total_mb = int(profile_data.get("ram_total_mb", 0))
        prof.ram_available_mb = int(profile_data.get("ram_available_mb", 0))
        prof.storage_total_gb = float(profile_data.get("storage_total_gb", 0))
        prof.storage_available_gb = float(profile_data.get("storage_available_gb", 0))
        prof.network_latency_ms = float(profile_data.get("network_latency_ms", 0))
        prof.os_info = profile_data.get("os_info")

    db.session.commit()
    return created(data=node.to_dict(), message="Noeud enregistré dans le cluster.")


# ──────────────────────────────────────────────
# POST /api/cluster/heartbeat
# ──────────────────────────────────────────────
@bp.route("/heartbeat", methods=["POST"])
@cluster_internal
def receive_heartbeat():
    """
    Recevoir un heartbeat d'un nœud worker (couche unicast).
    ---
    tags:
      - Cluster — API Interne
    summary: Heartbeat nœud → maître
    description: |
      **§2.3.11 du rapport — Couche 1 (unicast T_HB = 2 s).**

      Structure : `HBi,t = (uuid_i, k_i·t, CPU_i·t, RAM_i·t, q_i·t, Score_i·t, σ_i, L_i·t)`

      Le backend :
      1. Met à jour `last_heartbeat_at` du nœud
      2. Enregistre un enregistrement `Heartbeat` pour l'historique
      3. Remet le nœud en `actif` s'il était `en_panne` (récupération automatique)
      4. Met à jour RAM disponible et latence réseau dans le profil si fournis

      Le thread `NodeMonitor` (T_check = 2 s) détecte ensuite les nœuds silencieux :
      - Δt ≥ 4 s → SUSPECT (log seulement)
      - Δt ≥ 8 s → EN_PANNE
    security:
      - ClusterKey: []
    requestBody:
      required: true
      content:
        application/json:
          schema:
            $ref: '#/components/schemas/HeartbeatRequest'
    responses:
      200:
        description: Heartbeat enregistré.
        content:
          application/json:
            schema:
              allOf:
                - $ref: '#/components/schemas/ApiSuccessResponse'
              properties:
                data:
                  type: object
                  properties:
                    server_time:
                      type: string
                      format: date-time
                      description: Horodatage du serveur (pour synchronisation de l'horloge).
      400:
        description: Champ `uuid` manquant.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
      401:
        description: Clé cluster invalide.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
      404:
        description: Nœud non enregistré (appeler `/api/cluster/register` d'abord).
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
    """
    data = request.get_json(silent=True) or {}
    node_uuid = (data.get("uuid") or "").strip()
    if not node_uuid:
        return error("Champ 'uuid' requis.")

    node = Node.query.get(node_uuid)
    if node is None:
        return not_found("Noeud")

    now = datetime.now(timezone.utc)
    cpu_usage = float(data.get("cpu_usage", 0.0))
    ram_usage = float(data.get("ram_usage", 0.0))
    tasks_in_progress = int(data.get("tasks_in_progress", 0))
    score = float(data.get("score", 0.0))
    reported_status = str(data.get("status", NodeStatus.ACTIF.value))

    # Mettre à jour les métriques courantes du noeud
    node.last_heartbeat_at = now
    node.current_cpu_usage = cpu_usage
    node.current_ram_usage = ram_usage
    node.current_tasks_count = tasks_in_progress
    node.current_score = score

    # Remettre ACTIF si le noeud était EN_PANNE (récupération)
    if node.status == NodeStatus.EN_PANNE.value:
        node.status = NodeStatus.ACTIF.value

    # Mettre à jour le profil RAM disponible si fourni
    if node.profile and "ram_available_mb" in data:
        node.profile.ram_available_mb = int(data["ram_available_mb"])
    if node.profile and "network_latency_ms" in data:
        node.profile.network_latency_ms = float(data["network_latency_ms"])

    # Enregistrer le heartbeat
    hb = Heartbeat(
        node_uuid=node_uuid,
        received_at=now,
        cpu_usage=cpu_usage,
        ram_usage=ram_usage,
        tasks_in_progress=tasks_in_progress,
        score=score,
        reported_status=reported_status,
    )
    db.session.add(hb)
    db.session.commit()

    return success(message="Heartbeat reçu.", data={"server_time": now.isoformat()})


# ──────────────────────────────────────────────
# GET /api/cluster/tasks/next
# ──────────────────────────────────────────────
@bp.route("/tasks/next", methods=["GET"])
@cluster_internal
def next_task():
    """
    Demander la prochaine tâche disponible (polling worker).
    ---
    tags:
      - Cluster — API Interne
    summary: Prochaine tâche disponible
    description: |
      Un worker appelle cet endpoint en boucle pour obtenir la prochaine
      sous-tâche `en_attente` à exécuter (FIFO par `created_at`).

      Si une tâche est trouvée :
      - Elle passe en `assignee` avec l'UUID du worker
      - Le programme parent passe en `en_cours` si c'est la première tâche assignée

      Si aucune tâche n'est disponible, `data` est `null` (pas d'erreur 404).

      Le worker doit être dans un statut disponible (`actif` ou `surcharge` accepté).
    security:
      - ClusterKey: []
    parameters:
      - in: query
        name: node_uuid
        required: true
        schema:
          type: string
        description: UUID du nœud worker demandeur.
        example: node-dell-02
    responses:
      200:
        description: Tâche assignée ou aucune tâche disponible.
        content:
          application/json:
            schema:
              allOf:
                - $ref: '#/components/schemas/ApiSuccessResponse'
              properties:
                data:
                  nullable: true
                  type: object
                  properties:
                    task:
                      $ref: '#/components/schemas/TacheAtomiqueResponse'
                    programme_source_path:
                      type: string
                      description: Chemin relatif des sources du programme (pour le worker).
                      example: "abc123/source/"
      400:
        description: Paramètre `node_uuid` manquant.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
      401:
        description: Clé cluster invalide.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
      503:
        description: Nœud inconnu ou indisponible (en_panne, en_maintenance, eteint).
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
    """
    node_uuid = request.args.get("node_uuid", "").strip()
    if not node_uuid:
        return error("Paramètre 'node_uuid' requis.")

    node = Node.query.get(node_uuid)
    if node is None or not node.is_available():
        return error("Noeud inconnu ou indisponible.", status=503)

    task = TacheAtomique.query.filter_by(
        status=TacheStatus.EN_ATTENTE.value
    ).order_by(TacheAtomique.created_at.asc()).first()

    if task is None:
        return success(data=None, message="Aucune tâche en attente.")

    task.assign_to(node_uuid)

    # Passer le programme en EN_COURS si c'est la première tâche assignée
    prog = task.programme
    if prog.status == ProgrammeStatus.EN_DECOMPOSITION.value:
        prog.mark_running()

    db.session.commit()

    return success(
        data={
            "task": task.to_dict(),
            "programme_source_path": prog.source_rel_path,
        }
    )


# ──────────────────────────────────────────────
# POST /api/cluster/tasks/<id>/result
# ──────────────────────────────────────────────
@bp.route("/tasks/<task_id>/result", methods=["POST"])
@cluster_internal
def task_result(task_id: str):
    """
    Soumettre le résultat d'une sous-tâche terminée.
    ---
    tags:
      - Cluster — API Interne
    summary: Résultat de tâche
    description: |
      Le worker appelle cet endpoint quand il a terminé l'exécution d'une tâche.

      **Deux formats acceptés :**

      1. **JSON** : résultats sérialisables directement dans `output`
      2. **Multipart** : fichier binaire dans le champ `result_file`
         (stocké dans `STORAGE_ROOT/<user>/<programme>/results/<task_id>.result`)

      Après enregistrement, le backend vérifie si toutes les sous-tâches du programme
      sont terminées pour mettre à jour le statut global (`termine` ou `echec`).
    security:
      - ClusterKey: []
    parameters:
      - in: path
        name: task_id
        required: true
        schema:
          type: string
          format: uuid
        description: UUID de la sous-tâche.
    requestBody:
      required: true
      content:
        application/json:
          schema:
            $ref: '#/components/schemas/TaskResultRequest'
        multipart/form-data:
          schema:
            type: object
            required:
              - node_uuid
            properties:
              node_uuid:
                type: string
              output:
                type: string
                description: Résultat JSON sérialisé (optionnel si result_file fourni).
              result_file:
                type: string
                format: binary
                description: Fichier de résultat binaire.
    responses:
      200:
        description: Résultat enregistré.
        content:
          application/json:
            schema:
              allOf:
                - $ref: '#/components/schemas/ApiSuccessResponse'
              properties:
                data:
                  type: object
                  properties:
                    task_id:
                      type: string
      401:
        description: Clé cluster invalide.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
      403:
        description: Ce worker n'est pas assigné à cette tâche.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
      404:
        description: Tâche introuvable.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
    """
    task = TacheAtomique.query.get(task_id)
    if task is None:
        return not_found("Tâche atomique")

    node_uuid = ""
    output_data = ""

    if request.content_type and "multipart" in request.content_type:
        node_uuid = (request.form.get("node_uuid") or "").strip()
        output_data = request.form.get("output") or ""

        result_file = request.files.get("result_file")
        if result_file:
            from app.models.programme import Programme
            prog = task.programme
            user_id = prog.owner_id
            from app.services.storage import save_task_result
            try:
                rel_path, size = save_task_result(
                    user_id=user_id,
                    programme_id=prog.id,
                    task_id=task_id,
                    data=result_file.read(),
                )
                if not output_data:
                    output_data = json.dumps({"result_file": rel_path})
                prog.result_size_bytes = (prog.result_size_bytes or 0) + size
            except Exception as exc:
                return error(f"Impossible de sauvegarder le fichier résultat : {exc}")
    else:
        data = request.get_json(silent=True) or {}
        node_uuid = (data.get("node_uuid") or "").strip()
        output_data = json.dumps(data.get("output", ""))

    # Vérifier que c'est bien le bon worker
    if task.worker_node_uuid and task.worker_node_uuid != node_uuid:
        return error("Ce worker n'est pas assigné à cette tâche.", status=403)

    task.mark_done(output_data)
    db.session.commit()

    # Vérifier si toutes les sous-tâches du programme sont terminées
    _check_programme_completion(task.programme_id)

    return success(message="Résultat enregistré.", data={"task_id": task_id})


# ──────────────────────────────────────────────
# POST /api/cluster/tasks/<id>/error
# ──────────────────────────────────────────────
@bp.route("/tasks/<task_id>/error", methods=["POST"])
@cluster_internal
def task_error(task_id: str):
    """
    Signaler une erreur d'exécution sur une sous-tâche.
    ---
    tags:
      - Cluster — API Interne
    summary: Erreur de tâche
    description: |
      Le worker signale qu'il n'a pas pu exécuter la tâche.

      **Logique de retry :**
      - Si `attempts < max_attempts` (défaut : 3) → la tâche repasse en `en_attente`
        pour être réassignée à un autre worker
      - Si `attempts >= max_attempts` → la tâche passe définitivement en `echouee`

      Après mise à jour, le backend vérifie la complétion du programme parent.
    security:
      - ClusterKey: []
    parameters:
      - in: path
        name: task_id
        required: true
        schema:
          type: string
          format: uuid
        description: UUID de la sous-tâche en erreur.
    requestBody:
      required: true
      content:
        application/json:
          schema:
            $ref: '#/components/schemas/TaskErrorRequest'
    responses:
      200:
        description: Erreur enregistrée. Statut final de la tâche retourné.
        content:
          application/json:
            schema:
              allOf:
                - $ref: '#/components/schemas/ApiSuccessResponse'
              properties:
                data:
                  type: object
                  properties:
                    task_id:
                      type: string
                    status:
                      type: string
                      enum: [en_attente, echouee]
                      description: "`en_attente` si nouvelle tentative, `echouee` si épuisé."
      401:
        description: Clé cluster invalide.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
      404:
        description: Tâche introuvable.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
    """
    task = TacheAtomique.query.get(task_id)
    if task is None:
        return not_found("Tâche atomique")

    data = request.get_json(silent=True) or {}
    reason = str(data.get("reason") or "Erreur inconnue")

    task.mark_failed(reason)
    db.session.commit()

    _check_programme_completion(task.programme_id)

    return success(
        message="Erreur enregistrée.",
        data={"task_id": task_id, "status": task.status},
    )


# ──────────────────────────────────────────────
# POST /api/cluster/programme-result
# ──────────────────────────────────────────────
@bp.route("/programme-result", methods=["POST"])
@cluster_internal
def programme_result():
    """
    Callback du Receptionist : résultat d'exécution d'un programme soumis via /submit.
    ---
    tags:
      - Cluster — API Interne
    summary: Résultat d'un programme (push Receptionist)
    description: |
      Appelé par le Receptionist (`send_result_callback` dans
      `Receptionnist/reception.c`) une fois qu'il a reçu le `PROG_LOG` du
      programme relayé par le maître via le contrôleur. Le `programme_id` est
      l'UUID injecté comme marqueur `__parallax_prog_name__` au moment de la
      soumission (voir `app/api/tasks.py:_with_prog_name_marker`) — le
      Receptionist ne fait que le faire suivre tel quel, il ne connaît pas
      la notion de "programme" du backend.

      Seul le chemin de succès est câblé côté C aujourd'hui : l'agent maître
      n'envoie de `PROG_LOG` qu'après une compilation ET une exécution réussies
      (voir `Execution_Master/utils/master_thread.c`) — un échec de compilation
      ne produit aucun callback, et le programme reste `en_decomposition`
      indéfiniment. `status` est donc accepté en entrée pour anticiper un futur
      signal d'échec côté C, mais vaut `termine` en pratique pour l'instant.
    security:
      - ClusterKey: []
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required:
              - programme_id
              - log
            properties:
              programme_id:
                type: string
                format: uuid
              status:
                type: string
                enum: [termine, echec]
                default: termine
              log:
                type: string
    responses:
      200:
        description: Statut du programme mis à jour.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiSuccessResponse'
      400:
        description: Champ `programme_id` manquant.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
      401:
        description: Clé cluster invalide.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
      404:
        description: Programme introuvable (UUID inconnu ou déjà supprimé).
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/ApiErrorResponse'
    """
    data = request.get_json(silent=True) or {}
    programme_id = (data.get("programme_id") or "").strip()
    if not programme_id:
        return error("Champ 'programme_id' requis.")

    prog = Programme.query.get(programme_id)
    if prog is None:
        return not_found("Programme")

    log_content = data.get("log") or ""
    status = data.get("status") or ProgrammeStatus.TERMINE.value

    prog.execution_log = log_content
    if status == ProgrammeStatus.ECHEC.value:
        prog.mark_failed("Échec signalé par l'agent maître.")
    else:
        prog.mark_done()

    db.session.commit()

    return success(message=f"Programme {programme_id[:8]} marqué {prog.status}.")


# ──────────────────────────────────────────────
# Helpers internes
# ──────────────────────────────────────────────

def _check_programme_completion(programme_id: str) -> None:
    """
    Vérifie si toutes les sous-tâches d'un programme sont dans un état terminal.
    Met à jour le statut du programme en conséquence.
    """
    prog = Programme.query.get(programme_id)
    if prog is None or prog.is_terminal:
        return

    total = prog.taches.count()
    if total == 0:
        return

    done = prog.taches.filter_by(status=TacheStatus.TERMINEE.value).count()
    failed = prog.taches.filter_by(status=TacheStatus.ECHOUEE.value).count()
    pending = prog.taches.filter(
        TacheAtomique.status.in_([
            TacheStatus.EN_ATTENTE.value,
            TacheStatus.ASSIGNEE.value,
            TacheStatus.EN_COURS.value,
            TacheStatus.MIGREE.value,
        ])
    ).count()

    if pending > 0:
        return  # encore des tâches en cours

    if failed > 0:
        prog.mark_failed(
            f"{failed}/{total} sous-tâche(s) ont échoué après épuisement des tentatives."
        )
    else:
        prog.mark_done()

    db.session.commit()
