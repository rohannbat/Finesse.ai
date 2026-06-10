import uuid
from datetime import date, datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import SnapshotOut
from app.auth.crypto import encrypt_token
from app.auth.dependencies import get_current_user
from app.auth.security import create_state_token, decode_state_token
from app.database import get_db
from app.integrations import oura
from app.models import IntegrationToken, User
from app.services.snapshot_aggregator import sync_oura_snapshot

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


@router.get("/oura/connect")
def oura_connect(user: User = Depends(get_current_user)):
    """Returns the Oura authorization URL for the frontend to redirect to."""
    state = create_state_token(str(user.id), "oura")
    return {"authorize_url": oura.build_authorize_url(state)}


@router.get("/oura/callback")
def oura_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: Session = Depends(get_db),
):
    """OAuth redirect target — exchanges the code and stores encrypted tokens."""
    try:
        user_id = uuid.UUID(decode_state_token(state, "oura"))
    except (jwt.PyJWTError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid or expired state parameter")

    try:
        tokens = oura.exchange_code(code)
    except oura.OuraError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=tokens.get("expires_in", 86400))
    row = db.scalar(
        select(IntegrationToken).where(
            IntegrationToken.user_id == user_id,
            IntegrationToken.platform == "oura",
        )
    )
    if row is None:
        row = IntegrationToken(user_id=user_id, platform="oura", access_token="")
        db.add(row)

    row.access_token = encrypt_token(tokens["access_token"])
    if tokens.get("refresh_token"):
        row.refresh_token = encrypt_token(tokens["refresh_token"])
    row.expires_at = expires_at
    db.commit()

    return {"status": "connected", "platform": "oura"}


@router.post("/oura/sync", response_model=SnapshotOut)
def oura_sync(
    day: date | None = Query(default=None, description="Defaults to today (UTC)"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Manually trigger the daily snapshot aggregation for Oura.

    In production this runs from the 6am scheduler; the endpoint exists so the
    end-to-end slice is testable without cron.
    """
    target = day or datetime.now(timezone.utc).date()
    try:
        snapshot = sync_oura_snapshot(db, user, target)
    except oura.OuraError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return snapshot
