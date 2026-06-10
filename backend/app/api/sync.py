from datetime import datetime, timezone

import anthropic
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import SyncResponse
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models import DailyInsight, User
from app.services.insight_generator import generate_insight
from app.services.snapshot_aggregator import sync_all_connected

router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.post("", response_model=SyncResponse)
def sync_now(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """One-shot sync for polling clients (the iOS app calls this every ~30s).

    Pulls today's data from every connected integration (WHOOP, Oura, ...),
    and regenerates the Claude insight only when the underlying data actually
    changed — so frequent polling doesn't burn an API call per tick.
    """
    today = datetime.now(timezone.utc).date()

    snapshot, changed, source_errors = sync_all_connected(db, user, today)
    if snapshot is None:
        if source_errors:
            raise HTTPException(
                status_code=502,
                detail="; ".join(f"{p}: {e}" for p, e in source_errors.items()),
            )
        raise HTTPException(
            status_code=400,
            detail="No integrations connected — connect WHOOP or Oura first",
        )

    insight = db.scalar(
        select(DailyInsight).where(DailyInsight.user_id == user.id, DailyInsight.date == today)
    )
    insight_error = None
    if insight is None or changed:
        # Insight failure shouldn't fail the whole poll — the client still
        # gets fresh snapshot data plus the error to surface.
        try:
            insight = generate_insight(db, user, today, force=changed)
        except (anthropic.AnthropicError, RuntimeError) as exc:
            insight_error = str(exc)

    return SyncResponse(
        snapshot=snapshot,
        insight=insight,
        changed=changed,
        insight_error=insight_error,
        source_errors=source_errors,
    )
