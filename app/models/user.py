import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum

from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import String, DateTime, Boolean, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db


class UserRole(str, PyEnum):
    CHERCHEUR = "chercheur"
    ETUDIANT = "etudiant"
    GESTIONNAIRE = "gestionnaire"


class TokenBlocklist(db.Model):
    """JTI des tokens JWT révoqués."""
    __tablename__ = "token_blocklist"

    id: Mapped[int] = mapped_column(primary_key=True)
    jti: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    revoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class User(db.Model):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    _password_hash: Mapped[str] = mapped_column("password_hash", String(256), nullable=False)
    role: Mapped[str] = mapped_column(
        SAEnum(UserRole, values_callable=lambda x: [e.value for e in x], native_enum=False),
        nullable=False,
        default=UserRole.CHERCHEUR.value,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    storage_used_bytes: Mapped[int] = mapped_column(default=0, nullable=False)

    programmes: Mapped[list["Programme"]] = relationship(  # noqa: F821
        "Programme", back_populates="owner", lazy="dynamic", cascade="all, delete-orphan"
    )

    def set_password(self, password: str) -> None:
        if len(password) < 8:
            raise ValueError("Le mot de passe doit comporter au moins 8 caractères.")
        self._password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self._password_hash, password)

    @property
    def is_gestionnaire(self) -> bool:
        return self.role == UserRole.GESTIONNAIRE.value

    @property
    def is_chercheur_or_etudiant(self) -> bool:
        return self.role in (UserRole.CHERCHEUR.value, UserRole.ETUDIANT.value)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "role": self.role,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat(),
            "storage_used_bytes": self.storage_used_bytes,
        }

    def __repr__(self) -> str:
        return f"<User {self.username} ({self.role})>"
