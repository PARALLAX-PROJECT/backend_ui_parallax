import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum

from sqlalchemy import String, DateTime, Float, Integer, BigInteger, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db


class NodeRole(str, PyEnum):
    MASTER = "master"
    WORKER = "worker"
    CONTROLLER = "controller"
    REMPLACANT = "remplacant"


class NodeStatus(str, PyEnum):
    ACTIF = "actif"
    SURCHARGE = "surcharge"
    EN_PANNE = "en_panne"
    EN_MAINTENANCE = "en_maintenance"
    ETEINT = "eteint"


class NodeProfile(db.Model):
    """Profil matériel d'un noeud (CPU, RAM, réseau)."""
    __tablename__ = "node_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    node_uuid: Mapped[str] = mapped_column(
        String(36), ForeignKey("nodes.uuid", ondelete="CASCADE"), unique=True, nullable=False
    )
    cpu_cores: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    cpu_freq_mhz: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    arch_cpu: Mapped[str] = mapped_column(String(50), default="x86_64", nullable=False)
    ram_total_mb: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ram_available_mb: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    storage_total_gb: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    storage_available_gb: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    network_latency_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    os_info: Mapped[str | None] = mapped_column(String(100), nullable=True)

    node: Mapped["Node"] = relationship("Node", back_populates="profile")

    def election_score(self, cluster_maxima: dict) -> float:
        """
        Calcule le score d'élection du noeud selon la formule du rapport :
        score = a*(CPU_libre/CPU_max) * b*(RAM_libre/RAM_max)
                * c*(LATENCE_min/LATENCE_i) * d*(freq_i/freq_max)
        Poids : a=0.35, b=0.35, c=0.15, d=0.15
        """
        a, b, c, d = 0.35, 0.35, 0.15, 0.15
        cpu_score = (self.cpu_cores / max(cluster_maxima.get("cpu_cores", 1), 1))
        ram_score = (self.ram_available_mb / max(cluster_maxima.get("ram_available_mb", 1), 1))
        lat_min = cluster_maxima.get("latency_min_ms", 1.0)
        lat_i = max(self.network_latency_ms, 0.01)
        latency_score = lat_min / lat_i
        freq_score = self.cpu_freq_mhz / max(cluster_maxima.get("cpu_freq_mhz", 1), 1)
        return a * cpu_score + b * ram_score + c * latency_score + d * freq_score

    def to_dict(self) -> dict:
        return {
            "cpu_cores": self.cpu_cores,
            "cpu_freq_mhz": self.cpu_freq_mhz,
            "arch_cpu": self.arch_cpu,
            "ram_total_mb": self.ram_total_mb,
            "ram_available_mb": self.ram_available_mb,
            "storage_total_gb": self.storage_total_gb,
            "storage_available_gb": self.storage_available_gb,
            "network_latency_ms": self.network_latency_ms,
            "os_info": self.os_info,
        }


class Node(db.Model):
    """Noeud physique ou virtuel du cluster PARALLAX."""
    __tablename__ = "nodes"

    uuid: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    ip: Mapped[str] = mapped_column(String(45), unique=True, nullable=False, index=True)
    hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default=NodeRole.WORKER.value
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=NodeStatus.ACTIF.value
    )
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    # Métriques courantes (dernière valeur reçue via heartbeat)
    current_cpu_usage: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    current_ram_usage: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    current_tasks_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    profile: Mapped[NodeProfile | None] = relationship(
        "NodeProfile", back_populates="node",
        uselist=False, cascade="all, delete-orphan",
    )
    heartbeats: Mapped[list["Heartbeat"]] = relationship(  # noqa: F821
        "Heartbeat", back_populates="node",
        lazy="dynamic", cascade="all, delete-orphan",
    )
    assigned_tasks: Mapped[list["TacheAtomique"]] = relationship(  # noqa: F821
        "TacheAtomique", back_populates="worker_node", lazy="dynamic"
    )

    def is_available(self) -> bool:
        return self.status == NodeStatus.ACTIF.value

    def to_dict(self, include_profile: bool = True) -> dict:
        data: dict = {
            "uuid": self.uuid,
            "ip": self.ip,
            "hostname": self.hostname,
            "role": self.role,
            "status": self.status,
            "last_heartbeat_at": (
                self.last_heartbeat_at.isoformat() if self.last_heartbeat_at else None
            ),
            "registered_at": self.registered_at.isoformat(),
            "metrics": {
                "cpu_usage": self.current_cpu_usage,
                "ram_usage": self.current_ram_usage,
                "tasks_count": self.current_tasks_count,
                "score": self.current_score,
            },
        }
        if include_profile and self.profile:
            data["profile"] = self.profile.to_dict()
        return data

    def __repr__(self) -> str:
        return f"<Node {self.uuid[:8]} ip={self.ip} role={self.role} status={self.status}>"


class Heartbeat(db.Model):
    """Enregistrement d'un message heartbeat reçu d'un noeud."""
    __tablename__ = "heartbeats"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    node_uuid: Mapped[str] = mapped_column(
        String(36), ForeignKey("nodes.uuid", ondelete="CASCADE"), nullable=False, index=True
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    # Données brutes du heartbeat (cf. structure du rapport : HBi,t)
    cpu_usage: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    ram_usage: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    tasks_in_progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    reported_status: Mapped[str] = mapped_column(String(20), nullable=False, default="actif")

    node: Mapped[Node] = relationship("Node", back_populates="heartbeats")

    def to_dict(self) -> dict:
        return {
            "node_uuid": self.node_uuid,
            "received_at": self.received_at.isoformat(),
            "cpu_usage": self.cpu_usage,
            "ram_usage": self.ram_usage,
            "tasks_in_progress": self.tasks_in_progress,
            "score": self.score,
            "reported_status": self.reported_status,
        }
