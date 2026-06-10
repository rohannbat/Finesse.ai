from datetime import datetime, timezone

import anthropic
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import TodayResponse
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models import DailySnapshot, User
from app.services.insight_generator import generate_insight

router = APIRouter(prefix="/api/insights", tags=["insights"])


@router.get("/today", response_model=TodayResponse)
def get_today_insight(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Today's AI insight plus the raw snapshot it was generated from.

    Generates the insight on first request if the daily job hasn't run yet.
    """
    today = datetime.now(timezone.utc).date()

    snapshot = db.scalar(
        select(DailySnapshot).where(DailySnapshot.user_id == user.id, DailySnapshot.date == today)
    )
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail="No health data for today yet — connect Oura and run /api/integrations/oura/sync",
        )

    try:
        insight = generate_insight(db, user, today)
    except anthropic.APIError as exc:
        raise HTTPException(status_code=502, detail=f"Insight generation failed: {exc.message}")

    return TodayResponse(insight=insight, snapshot=snapshot)
