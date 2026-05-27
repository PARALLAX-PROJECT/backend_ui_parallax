import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum

from sqlalchemy import String, DateTime, Text, BigInteger, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db


class ProgrammeStatus(str, PyEnum):
    SOUMIS = "soumis"
    EN_DECOMPOSITION = "en_decomposition"
    EN_COURS = "en_cours"
    TERMINE = "termine"
    ECHEC = "echec"
    ANNULE = "annule"


class Programme(db.Model):
    """
    Programme soumis par un chercheur pour exécution distribuée.
    Correspond à 'Programme' du diagramme de classes métier.
    """
    __tablename__ = "programmes"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=ProgrammeStatus.SOUMIS.value
    )

    # Chemins sur le système de fichiers du noeud maître
    # Stockés relativement à STORAGE_ROOT pour portabilité
    source_rel_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    result_rel_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Métadonnées fichier
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    result_size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    # Timings
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_log: Mapped[str | None] = mapped_column(Text, nullable=True)

    owner: Mapped["User"] = relationship("User", back_populates="programmes")  # noqa: F821
    taches: Mapped[list["TacheAtomique"]] = relationship(  # noqa: F821
        "TacheAtomique",
        back_populates="programme",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    def mark_submitted(self) -> None:
        self.status = ProgrammeStatus.EN_DECOMPOSITION.value
        self.submitted_at = datetime.now(timezone.utc)

    def mark_running(self) -> None:
        self.status = ProgrammeStatus.EN_COURS.value
        self.started_at = datetime.now(timezone.utc)

    def mark_done(self) -> None:
        self.status = ProgrammeStatus.TERMINE.value
        self.completed_at = datetime.now(timezone.utc)

    def mark_failed(self, reason: str) -> None:
        self.status = ProgrammeStatus.ECHEC.value
        self.completed_at = datetime.now(timezone.utc)
        self.error_message = reason

    def mark_cancelled(self) -> None:
        self.status = ProgrammeStatus.ANNULE.value
        self.completed_at = datetime.now(timezone.utc)

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            ProgrammeStatus.TERMINE.value,
            ProgrammeStatus.ECHEC.value,
            ProgrammeStatus.ANNULE.value,
        )

    def progress(self) -> dict:
        """Retourne le pourcentage d'avancement basé sur les sous-tâches."""
        from app.models.tache import TacheStatus
        total = self.taches.count()
        if total == 0:
            return {"total": 0, "done": 0, "failed": 0, "percent": 0}
        done = self.taches.filter_by(status=TacheStatus.TERMINEE.value).count()
        failed = self.taches.filter_by(status=TacheStatus.ECHOUEE.value).count()
        return {
            "total": total,
            "done": done,
            "failed": failed,
            "percent": round(done / total * 100, 1),
        }

    def to_dict(self, include_progress: bool = False) -> dict:
        data: dict = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "owner_id": self.owner_id,
            "status": self.status,
            "original_filename": self.original_filename,
            "source_size_bytes": self.source_size_bytes,
            "result_size_bytes": self.result_size_bytes,
            "uploaded_at": self.uploaded_at.isoformat(),
            "submitted_at": self.submitted_at.isoformat() if self.submitted_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error_message": self.error_message,
        }
        if include_progress:
            data["progress"] = self.progress()
        return data

    def __repr__(self) -> str:
        return f"<Programme {self.id[:8]} name={self.name!r} status={self.status}>"
