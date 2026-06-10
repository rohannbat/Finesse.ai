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
from app.integrations import IntegrationError, oura, whoop
from app.models import IntegrationToken, User
from app.services.snapshot_aggregator import PLATFORMS, sync_platform_snapshot

router = APIRouter(prefix="/api/integrations", tags=["integrations"])

OAUTH_MODULES = {"whoop": whoop, "oura": oura}


def _require_platform(platform: str):
    if platform not in OAUTH_MODULES:
        raise HTTPException(status_code=404, detail=f"Unknown platform: {platform}")
    return OAUTH_MODULES[platform]


@router.get("/{platform}/connect")
def connect(platform: str, user: User = Depends(get_current_user)):
    """Returns the platform's OAuth authorization URL for the client to open."""
    module = _require_platform(platform)
    state = create_state_token(str(user.id), platform)
    return {"authorize_url": module.build_authorize_url(state)}


def _handle_callback(platform: str, code: str, state: str, db: Session) -> dict:
    module = _require_platform(platform)
    try:
        user_id = uuid.UUID(decode_state_token(state, platform))
    except (jwt.PyJWTError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid or expired state parameter")

    try:
        tokens = module.exchange_code(code)
    except IntegrationError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=tokens.get("expires_in", 3600))
    row = db.scalar(
        select(IntegrationToken).where(
            IntegrationToken.user_id == user_id,
            IntegrationToken.platform == platform,
        )
    )
    if row is None:
        row = IntegrationToken(user_id=user_id, platform=platform, access_token="")
        db.add(row)

    row.access_token = encrypt_token(tokens["access_token"])
    if tokens.get("refresh_token"):
        row.refresh_token = encrypt_token(tokens["refresh_token"])
    row.expires_at = expires_at
    db.commit()

    return {"status": "connected", "platform": platform}


# OAuth redirect targets — registered as distinct paths because each is a
# separately whitelisted redirect URI in the platform's developer portal.
@router.get("/whoop/callback")
def whoop_callback(code: str = Query(...), state: str = Query(...), db: Session = Depends(get_db)):
    return _handle_callback("whoop", code, state, db)


@router.get("/oura/callback")
def oura_callback(code: str = Query(...), state: str = Query(...), db: Session = Depends(get_db)):
    return _handle_callback("oura", code, state, db)


@router.post("/{platform}/sync", response_model=SnapshotOut)
def platform_sync(
    platform: str,
    day: date | None = Query(default=None, description="Defaults to today (UTC)"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Manually trigger snapshot aggregation for one platform.

    The iOS app uses POST /api/sync (all connected platforms at once); this
    endpoint is for targeted backfills, e.g. ?day=2026-06-01.
    """
    if platform not in PLATFORMS:
        raise HTTPException(status_code=404, detail=f"Unknown platform: {platform}")
    target = day or datetime.now(timezone.utc).date()
    try:
        snapshot, _ = sync_platform_snapshot(db, user, target, platform)
    except IntegrationError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return snapshot
