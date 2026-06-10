import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Integer, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DailySnapshot(Base):
    """One row per user per day — normalised across all integrations."""

    __tablename__ = "daily_snapshot"
    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_snapshot_user_date"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)

    # Sleep
    sleep_duration_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sleep_efficiency_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    deep_sleep_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rem_sleep_min: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Recovery / Readiness
    hrv_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    resting_hr: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recovery_score: Mapped[float | None] = mapped_column(Float, nullable=True)  # normalised 0-100

    # Activity
    strain_score: Mapped[float | None] = mapped_column(Float, nullable=True)  # WHOOP 0-21
    active_calories: Mapped[int | None] = mapped_column(Integer, nullable=True)
    steps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    workout_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Nutrition
    calories_consumed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    protein_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    carbs_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    fat_g: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Metadata
    sources: Mapped[dict] = mapped_column(JSON, default=dict)  # {"oura": true, ...}
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DailyInsight(Base):
    """AI-generated insight per user per day."""

    __tablename__ = "daily_insight"
    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_insight_user_date"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    insight_text: Mapped[str] = mapped_column(Text)
    flags: Mapped[dict] = mapped_column(JSON, default=dict)  # {"hrv_drop": true, ...}
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
