from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import NutritionTodayResponse
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models import DailySnapshot, User

router = APIRouter(prefix="/api/nutrition", tags=["nutrition"])

# Writes go through POST /api/food (meal-level entries that roll up into
# the snapshot); this is the cheap read for energy-balance rendering.


@router.get("/today", response_model=NutritionTodayResponse)
def nutrition_today(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Today's nutrition totals plus active_calories, so a client can render
    energy balance without triggering a full sync. All-null if nothing has
    been logged or synced yet."""
    today = datetime.now(timezone.utc).date()
    snapshot = db.scalar(
        select(DailySnapshot).where(DailySnapshot.user_id == user.id, DailySnapshot.date == today)
    )
    return NutritionTodayResponse(
        day=today,
        calories_consumed=snapshot.calories_consumed if snapshot else None,
        protein_g=snapshot.protein_g if snapshot else None,
        carbs_g=snapshot.carbs_g if snapshot else None,
        fat_g=snapshot.fat_g if snapshot else None,
        active_calories=snapshot.active_calories if snapshot else None,
    )
