"""Aggregate platform data into the normalised daily_snapshot row.

Designed for frequent polling: upserts on (user_id, date), merges sources,
and reports whether anything actually changed so callers can skip downstream
work (e.g. insight regeneration) on no-op syncs.
"""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.crypto import decrypt_token, encrypt_token
from app.integrations import IntegrationError, oura, whoop
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

WHOOP_FIELDS = (
    "sleep_duration_min",
    "sleep_efficiency_pct",
    "deep_sleep_min",
    "rem_sleep_min",
    "hrv_ms",
    "resting_hr",
    "recovery_score",
    "strain_score",
    "active_calories",
    "workout_minutes",
)

# Module references (not bound functions) so tests can patch
# `<module>.fetch_daily_data` and have it take effect here.
PLATFORMS: dict[str, dict] = {
    "whoop": {"module": whoop, "fields": WHOOP_FIELDS},
    "oura": {"module": oura, "fields": OURA_FIELDS},
}


def _get_valid_access_token(db: Session, token_row: IntegrationToken) -> str:
    """Return a usable access token, refreshing (and re-encrypting) if expired."""
    now = datetime.now(timezone.utc)
    expires_at = token_row.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at is None or expires_at > now + timedelta(minutes=5):
        return decrypt_token(token_row.access_token)

    if not token_row.refresh_token:
        raise IntegrationError(f"{token_row.platform} token expired and no refresh token stored")

    module = PLATFORMS[token_row.platform]["module"]
    fresh = module.refresh_access_token(decrypt_token(token_row.refresh_token))
    token_row.access_token = encrypt_token(fresh["access_token"])
    if fresh.get("refresh_token"):
        token_row.refresh_token = encrypt_token(fresh["refresh_token"])
    token_row.expires_at = now + timedelta(seconds=fresh.get("expires_in", 86400))
    db.commit()
    return fresh["access_token"]


def sync_platform_snapshot(
    db: Session, user: User, day: date, platform: str
) -> tuple[DailySnapshot, bool]:
    """Fetch one platform's data for `day` and upsert into the user's snapshot.

    Returns (snapshot, changed).
    """
    if platform not in PLATFORMS:
        raise IntegrationError(f"Unknown platform: {platform}")

    token_row = db.scalar(
        select(IntegrationToken).where(
            IntegrationToken.user_id == user.id,
            IntegrationToken.platform == platform,
        )
    )
    if token_row is None:
        raise IntegrationError(f"{platform} is not connected for this user")

    access_token = _get_valid_access_token(db, token_row)
    config = PLATFORMS[platform]
    data = config["module"].fetch_daily_data(access_token, day)

    snapshot = db.scalar(
        select(DailySnapshot).where(
            DailySnapshot.user_id == user.id,
            DailySnapshot.date == day,
        )
    )
    changed = False
    if snapshot is None:
        snapshot = DailySnapshot(user_id=user.id, date=day, sources={})
        db.add(snapshot)
        changed = True

    for field in config["fields"]:
        new_value = data.get(field)
        if new_value is not None and getattr(snapshot, field) != new_value:
            setattr(snapshot, field, new_value)
            changed = True

    sources = dict(snapshot.sources or {})
    sources[platform] = True
    snapshot.sources = sources

    db.commit()
    db.refresh(snapshot)
    return snapshot, changed


def sync_all_connected(
    db: Session, user: User, day: date
) -> tuple[DailySnapshot | None, bool, dict[str, str]]:
    """Sync every platform the user has connected.

    Returns (snapshot, changed, errors). One platform failing doesn't stop
    the others; per-platform errors are returned keyed by platform name.
    Snapshot is None only if no platform produced data.
    """
    connected = db.scalars(
        select(IntegrationToken.platform).where(IntegrationToken.user_id == user.id)
    ).all()

    snapshot: DailySnapshot | None = None
    changed = False
    errors: dict[str, str] = {}
    for platform in connected:
        if platform not in PLATFORMS:
            continue
        try:
            snapshot, platform_changed = sync_platform_snapshot(db, user, day, platform)
            changed = changed or platform_changed
        except IntegrationError as exc:
            errors[platform] = str(exc)

    return snapshot, changed, errors


def sync_oura_snapshot(db: Session, user: User, day: date) -> tuple[DailySnapshot, bool]:
    return sync_platform_snapshot(db, user, day, "oura")


def sync_whoop_snapshot(db: Session, user: User, day: date) -> tuple[DailySnapshot, bool]:
    return sync_platform_snapshot(db, user, day, "whoop")
