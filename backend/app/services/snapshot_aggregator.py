"""Aggregate platform data into the normalised daily_snapshot row.

Designed to run once per day per user (e.g. 6am cron) but safe to re-run:
it upserts on (user_id, date) and merges sources.
"""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.crypto import decrypt_token, encrypt_token
from app.integrations import oura
from app.models import DailySnapshot, IntegrationToken, User

OURA_FIELDS = (
    "sleep_duration_min",
    "sleep_efficiency_pct",
    "deep_sleep_min",
    "rem_sleep_min",
    "hrv_ms",
    "resting_hr",
    "recovery_score",
    "active_calories",
    "steps",
    "workout_minutes",
)


def _get_valid_oura_access_token(db: Session, token_row: IntegrationToken) -> str:
    """Return a usable access token, refreshing (and re-encrypting) if expired."""
    now = datetime.now(timezone.utc)
    expires_at = token_row.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at is None or expires_at > now + timedelta(minutes=5):
        return decrypt_token(token_row.access_token)

    if not token_row.refresh_token:
        raise oura.OuraError("Oura token expired and no refresh token stored")

    fresh = oura.refresh_access_token(decrypt_token(token_row.refresh_token))
    token_row.access_token = encrypt_token(fresh["access_token"])
    if fresh.get("refresh_token"):
        token_row.refresh_token = encrypt_token(fresh["refresh_token"])
    token_row.expires_at = now + timedelta(seconds=fresh.get("expires_in", 86400))
    db.commit()
    return fresh["access_token"]


def sync_oura_snapshot(db: Session, user: User, day: date) -> DailySnapshot:
    """Fetch Oura data for `day` and upsert it into the user's snapshot."""
    token_row = db.scalar(
        select(IntegrationToken).where(
            IntegrationToken.user_id == user.id,
            IntegrationToken.platform == "oura",
        )
    )
    if token_row is None:
        raise oura.OuraError("Oura is not connected for this user")

    access_token = _get_valid_oura_access_token(db, token_row)
    data = oura.fetch_daily_data(access_token, day)

    snapshot = db.scalar(
        select(DailySnapshot).where(
            DailySnapshot.user_id == user.id,
            DailySnapshot.date == day,
        )
    )
    if snapshot is None:
        snapshot = DailySnapshot(user_id=user.id, date=day, sources={})
        db.add(snapshot)

    for field in OURA_FIELDS:
        if data.get(field) is not None:
            setattr(snapshot, field, data[field])

    sources = dict(snapshot.sources or {})
    sources["oura"] = True
    snapshot.sources = sources

    db.commit()
    db.refresh(snapshot)
    return snapshot
