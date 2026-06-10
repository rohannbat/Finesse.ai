import uuid
from datetime import date, datetime, timezone

import anthropic
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import FoodEntryCreate, FoodListResponse, FoodLogResponse
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models import FoodEntry, User
from app.services.insight_generator import generate_insight
from app.services.nutrition_service import recompute_day_totals

router = APIRouter(prefix="/api/food", tags=["food"])


def _day_entries(db: Session, user: User, day: date) -> list[FoodEntry]:
    return list(
        db.scalars(
            select(FoodEntry)
            .where(FoodEntry.user_id == user.id, FoodEntry.date == day)
            .order_by(FoodEntry.created_at)
        )
    )


def _respond_after_mutation(db: Session, user: User, day: date) -> FoodLogResponse:
    """Recompute the day's totals and regenerate the insight if they moved —
    the same change-detection contract as wearable syncs."""
    snapshot, changed = recompute_day_totals(db, user, day)

    insight = None
    insight_error = None
    if snapshot is not None:
        try:
            insight = generate_insight(db, user, day, force=changed)
        except (anthropic.AnthropicError, RuntimeError) as exc:
            insight_error = str(exc)

    return FoodLogResponse(
        entries=_day_entries(db, user, day),
        snapshot=snapshot,
        insight=insight,
        changed=changed,
        insight_error=insight_error,
    )


@router.post("", response_model=FoodLogResponse)
def add_food(
    body: FoodEntryCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Log one food/meal. The day's totals in daily_snapshot are recomputed
    as the sum of the day's entries (entries are the source of truth for
    nutrition), and the insight regenerates if totals changed."""
    day = body.day or datetime.now(timezone.utc).date()
    entry = FoodEntry(
        user_id=user.id,
        date=day,
        name=body.name.strip(),
        calories=body.calories,
        protein_g=body.protein_g,
        carbs_g=body.carbs_g,
        fat_g=body.fat_g,
    )
    db.add(entry)
    db.commit()
    return _respond_after_mutation(db, user, day)


@router.get("", response_model=FoodListResponse)
def list_food(
    day: date | None = Query(default=None, description="Defaults to today (UTC)"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target = day or datetime.now(timezone.utc).date()
    return FoodListResponse(day=target, entries=_day_entries(db, user, target))


@router.delete("/{entry_id}", response_model=FoodLogResponse)
def delete_food(
    entry_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    entry = db.scalar(
        select(FoodEntry).where(FoodEntry.id == entry_id, FoodEntry.user_id == user.id)
    )
    if entry is None:
        raise HTTPException(status_code=404, detail="Food entry not found")
    day = entry.date
    db.delete(entry)
    db.commit()
    return _respond_after_mutation(db, user, day)
