import uuid
from datetime import date

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.constants import BMR_KCAL


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class SnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    date: date
    sleep_duration_min: int | None
    sleep_efficiency_pct: float | None
    deep_sleep_min: int | None
    rem_sleep_min: int | None
    hrv_ms: float | None
    resting_hr: int | None
    recovery_score: float | None
    strain_score: float | None
    active_calories: int | None
    steps: int | None
    workout_minutes: int | None
    calories_consumed: int | None
    protein_g: float | None
    carbs_g: float | None
    fat_g: float | None
    sources: dict


class InsightOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    date: date
    insight_text: str
    flags: dict


class TodayResponse(BaseModel):
    insight: InsightOut
    snapshot: SnapshotOut


class SyncResponse(BaseModel):
    """Combined payload for polling clients (one round trip per poll)."""

    snapshot: SnapshotOut
    insight: InsightOut | None
    changed: bool
    insight_error: str | None = None
    source_errors: dict[str, str] = {}
    # Echoed so the client renders energy balance with the same constant
    # the backend flags against (see app/constants.py).
    bmr_kcal: int = BMR_KCAL


class NutritionLogRequest(BaseModel):
    day: date | None = None  # defaults to today (UTC)
    calories: int = Field(ge=0, le=10000)
    protein_g: float = Field(ge=0, le=1000)
    carbs_g: float | None = Field(default=None, ge=0, le=1000)
    fat_g: float | None = Field(default=None, ge=0, le=1000)


class NutritionTodayResponse(BaseModel):
    day: date
    calories_consumed: int | None
    protein_g: float | None
    carbs_g: float | None
    fat_g: float | None
    active_calories: int | None
    bmr_kcal: int = BMR_KCAL
