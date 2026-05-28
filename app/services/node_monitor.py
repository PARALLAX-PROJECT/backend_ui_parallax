"""
Service de surveillance des noeuds PARALLAX.

Un thread de fond inspecte périodiquement la table `nodes` et met à jour
les statuts selon les seuils du protocole heartbeat (rapport §2.3.11) :
  - ALIVE   → SUSPECTED  si Δt ≥ T_suspect
  - SUSPECTED → FAILED   si Δt ≥ T_failed
  - FAILED  → ALIVE      si un heartbeat est reçu (cf. api/cluster.py)
"""
import logging
import threading
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class NodeMonitor:
    def __init__(self):
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._app = None

    def init_app(self, app) -> None:
        self._app = app

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="parallax-node-monitor", daemon=True
        )
        self._thread.start()
        logger.info("NodeMonitor démarré.")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("NodeMonitor arrêté.")

    def _run(self) -> None:
        while not self._stop_event.wait(timeout=2.0):
            try:
                self._check_nodes()
            except Exception:
                logger.exception("Erreur dans NodeMonitor._check_nodes()")

    def _check_nodes(self) -> None:
        with self._app.app_context():
            from app.extensions import db
            from app.models.node import Node, NodeStatus

            cfg = self._app.config
            t_suspect = cfg["HB_SUSPECT_THRESHOLD_S"]
            t_failed = cfg["HB_FAILED_THRESHOLD_S"]
            overload_high = cfg["HB_OVERLOAD_HIGH"]
            overload_low = cfg["HB_OVERLOAD_LOW"]

            now = datetime.utcnow()
            nodes = Node.query.filter(
                Node.status != NodeStatus.ETEINT.value,
                Node.status != NodeStatus.EN_MAINTENANCE.value,
            ).all()

            changed = False
            for node in nodes:
                if node.last_heartbeat_at is None:
                    continue

                delta = (now - node.last_heartbeat_at).total_seconds()
                prev_status = node.status

                if delta >= t_failed:
                    if node.status != NodeStatus.EN_PANNE.value:
                        node.status = NodeStatus.EN_PANNE.value
                        logger.warning(
                            "Noeud %s (%s) marqué EN_PANNE (Δt=%.1fs)",
                            node.uuid[:8], node.ip, delta,
                        )
                        changed = True
                elif delta >= t_suspect:
                    # Phase 1 : suspicion (pas encore de changement de statut
                    # définitif, on laisse le statut actuel mais on le log)
                    if node.status == NodeStatus.ACTIF.value:
                        logger.debug(
                            "Noeud %s (%s) SUSPECT (Δt=%.1fs)",
                            node.uuid[:8], node.ip, delta,
                        )
                else:
                    # Noeud actif — vérifier la charge
                    if node.status == NodeStatus.ACTIF.value:
                        if (
                            node.current_cpu_usage >= overload_high
                            or node.current_ram_usage >= overload_high
                        ):
                            node.status = NodeStatus.SURCHARGE.value
                            logger.info(
                                "Noeud %s marqué SURCHARGE (cpu=%.0f%% ram=%.0f%%)",
                                node.uuid[:8],
                                node.current_cpu_usage * 100,
                                node.current_ram_usage * 100,
                            )
                            changed = True
                    elif node.status == NodeStatus.SURCHARGE.value:
                        if (
                            node.current_cpu_usage < overload_low
                            and node.current_ram_usage < overload_low
                        ):
                            node.status = NodeStatus.ACTIF.value
                            logger.info(
                                "Noeud %s retour ACTIF (cpu=%.0f%% ram=%.0f%%)",
                                node.uuid[:8],
                                node.current_cpu_usage * 100,
                                node.current_ram_usage * 100,
                            )
                            changed = True
                    elif node.status == NodeStatus.EN_PANNE.value:
                        # Récupération : heartbeat reçu, delta < t_suspect
                        node.status = NodeStatus.ACTIF.value
                        logger.info("Noeud %s récupéré → ACTIF", node.uuid[:8])
                        changed = True

            if changed:
                db.session.commit()


monitor = NodeMonitor()
