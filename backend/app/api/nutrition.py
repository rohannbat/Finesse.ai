from datetime import datetime, timezone

import anthropic
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import NutritionLogRequest, NutritionTodayResponse, SyncResponse
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models import DailySnapshot, User
from app.services.insight_generator import generate_insight

router = APIRouter(prefix="/api/nutrition", tags=["nutrition"])


@router.post("", response_model=SyncResponse)
def log_nutrition(
    body: NutritionLogRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Log the day's nutrition totals.

    **Replace semantics, not accumulate**: each POST sets the day's totals
    outright. Log once at end of day, or re-POST running totals during the
    day — the latest POST wins. Optional fields (carbs_g, fat_g) that are
    omitted are left unchanged, not cleared.

    Upserts into the same daily_snapshot row as wearable syncs (creating it
    if no sync has happened yet that day) and merges "manual" into sources.
    If values changed, the Claude insight is regenerated inline and returned
    — same response shape as POST /api/sync.
    """
    day = body.day or datetime.now(timezone.utc).date()

    snapshot = db.scalar(
        select(DailySnapshot).where(DailySnapshot.user_id == user.id, DailySnapshot.date == day)
    )
    changed = False
    if snapshot is None:
        snapshot = DailySnapshot(user_id=user.id, date=day, sources={})
        db.add(snapshot)
        changed = True

    updates = {
        "calories_consumed": body.calories,
        "protein_g": body.protein_g,
        "carbs_g": body.carbs_g,
        "fat_g": body.fat_g,
    }
    for field, value in updates.items():
        if value is not None and getattr(snapshot, field) != value:
            setattr(snapshot, field, value)
            changed = True

    sources = dict(snapshot.sources or {})
    sources["manual"] = True
    snapshot.sources = sources

    db.commit()
    db.refresh(snapshot)

    # Same change-detection path as wearable syncs: regenerate the insight
    # only when the data actually moved (or none exists yet for the day).
    insight = None
    insight_error = None
    try:
        insight = generate_insight(db, user, day, force=changed)
    except (anthropic.AnthropicError, RuntimeError) as exc:
        insight_error = str(exc)

    return SyncResponse(
        snapshot=snapshot,
        insight=insight,
        changed=changed,
        insight_error=insight_error,
    )


@router.get("/today", response_model=NutritionTodayResponse)
def nutrition_today(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Today's nutrition fields plus active_calories, so a client can render
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
