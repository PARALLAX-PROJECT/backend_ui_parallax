import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum

from sqlalchemy import String, DateTime, Text, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db


class TacheStatus(str, PyEnum):
    EN_ATTENTE = "en_attente"
    ASSIGNEE = "assignee"
    EN_COURS = "en_cours"
    TERMINEE = "terminee"
    ECHOUEE = "echouee"
    MIGREE = "migree"


class TacheAtomique(db.Model):
    """
    Sous-tâche atomique issue de la décomposition d'un Programme.
    Correspond à 'TacheAtomique' du diagramme de classes métier.
    """
    __tablename__ = "taches_atomiques"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    programme_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("programmes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Identifiant de l'annotation @parallax.split qui a généré cette tâche
    annotation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Nom de la fonction à exécuter
    function_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TacheStatus.EN_ATTENTE.value, index=True
    )
    worker_node_uuid: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("nodes.uuid", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Données d'entrée (JSON sérialisé)
    data_input: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Données de sortie (JSON sérialisé)
    data_output: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Comptage des tentatives
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    programme: Mapped["Programme"] = relationship(  # noqa: F821
        "Programme", back_populates="taches"
    )
    worker_node: Mapped["Node | None"] = relationship(  # noqa: F821
        "Node", back_populates="assigned_tasks"
    )

    def assign_to(self, node_uuid: str) -> None:
        self.worker_node_uuid = node_uuid
        self.status = TacheStatus.ASSIGNEE.value
        self.assigned_at = datetime.now(timezone.utc)
        self.attempts += 1

    def mark_running(self) -> None:
        self.status = TacheStatus.EN_COURS.value
        self.started_at = datetime.now(timezone.utc)

    def mark_done(self, output_data: str) -> None:
        self.status = TacheStatus.TERMINEE.value
        self.data_output = output_data
        self.completed_at = datetime.now(timezone.utc)

    def mark_failed(self, reason: str) -> None:
        self.error_message = reason
        if self.attempts >= self.max_attempts:
            self.status = TacheStatus.ECHOUEE.value
        else:
            # Retour en attente pour réassignation
            self.status = TacheStatus.EN_ATTENTE.value
            self.worker_node_uuid = None
        self.completed_at = datetime.now(timezone.utc)

    def mark_migrated(self) -> None:
        self.status = TacheStatus.MIGREE.value
        self.worker_node_uuid = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "programme_id": self.programme_id,
            "annotation_id": self.annotation_id,
            "function_name": self.function_name,
            "status": self.status,
            "worker_node_uuid": self.worker_node_uuid,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "created_at": self.created_at.isoformat(),
            "assigned_at": self.assigned_at.isoformat() if self.assigned_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error_message": self.error_message,
        }

    def __repr__(self) -> str:
        return (
            f"<TacheAtomique {self.id[:8]} prog={self.programme_id[:8]} "
            f"status={self.status} attempts={self.attempts}>"
        )
