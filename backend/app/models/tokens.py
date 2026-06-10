import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class IntegrationToken(Base):
    """Per-user OAuth tokens for a connected platform.

    access_token / refresh_token are stored Fernet-encrypted at rest —
    use app.auth.crypto.encrypt_token / decrypt_token.
    """

    __tablename__ = "integration_tokens"
    __table_args__ = (UniqueConstraint("user_id", "platform", name="uq_integration_user_platform"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    platform: Mapped[str] = mapped_column(String(32))  # "oura", "mfp", "whoop", ...
    access_token: Mapped[str] = mapped_column(Text)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
