"""Roll up food entries into the daily_snapshot nutrition columns.

Food entries are the single source of truth for nutrition: every entry
mutation recomputes the day's totals as the sum of that day's entries,
using the same change-detection contract as wearable syncs so the insight
only regenerates when totals actually moved.
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DailySnapshot, FoodEntry, User

NUTRITION_FIELDS = ("calories_consumed", "protein_g", "carbs_g", "fat_g")


def _sum_or_none(values: list) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def recompute_day_totals(
    db: Session, user: User, day: date
) -> tuple[DailySnapshot | None, bool]:
    """Recompute the day's nutrition totals from its food entries.

    Returns (snapshot, changed). Creates the snapshot row if entries exist
    but no wearable sync has happened yet; clears the nutrition columns
    (and the "manual" source) when the last entry of the day is deleted.
    """
    entries = db.scalars(
        select(FoodEntry).where(FoodEntry.user_id == user.id, FoodEntry.date == day)
    ).all()

    snapshot = db.scalar(
        select(DailySnapshot).where(
            DailySnapshot.user_id == user.id, DailySnapshot.date == day
        )
    )

    changed = False
    if snapshot is None:
        if not entries:
            return None, False
        snapshot = DailySnapshot(user_id=user.id, date=day, sources={})
        db.add(snapshot)
        changed = True

    totals = {
        "calories_consumed": sum(e.calories for e in entries) if entries else None,
        "protein_g": _sum_or_none([e.protein_g for e in entries]),
        "carbs_g": _sum_or_none([e.carbs_g for e in entries]),
        "fat_g": _sum_or_none([e.fat_g for e in entries]),
    }
    for field, value in totals.items():
        if getattr(snapshot, field) != value:
            setattr(snapshot, field, value)
            changed = True

    sources = dict(snapshot.sources or {})
    if entries and not sources.get("manual"):
        sources["manual"] = True
        changed = True
    elif not entries and sources.get("manual"):
        sources.pop("manual")
        changed = True
    snapshot.sources = sources

    db.commit()
    db.refresh(snapshot)
    return snapshot, changed
