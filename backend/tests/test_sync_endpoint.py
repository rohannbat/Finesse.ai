"""Tests for POST /api/sync — the 30s polling endpoint used by the iOS app.

Verifies the change-detection contract: Claude is only called when the
snapshot data actually changed (or no insight exists yet).
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.auth.crypto import encrypt_token
from app.database import SessionLocal
from app.models import IntegrationToken, User

DAY_V1 = {
    "sleep_duration_min": 400,
    "sleep_efficiency_pct": 88.0,
    "deep_sleep_min": 70,
    "rem_sleep_min": 90,
    "hrv_ms": 50.0,
    "resting_hr": 55,
    "recovery_score": 70.0,
    "active_calories": 300,
    "steps": 6000,
    "workout_minutes": 20,
}
# Same day, after the ring synced again: more steps/calories
DAY_V2 = {**DAY_V1, "steps": 9500, "active_calories": 450}


def _fake_claude(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    return MagicMock(content=[block], stop_reason="end_turn")


@pytest.fixture(scope="module")
def auth(client):
    email = f"sync-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/auth/register", json={"email": email, "password": "longpassword1"})
    assert r.status_code == 201, r.text
    token = r.json()["access_token"]
    with SessionLocal() as db:
        user = db.query(User).filter_by(email=email).one()
        db.add(
            IntegrationToken(
                user_id=user.id,
                platform="oura",
                access_token=encrypt_token("oura-access"),
                refresh_token=encrypt_token("oura-refresh"),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=12),
            )
        )
        db.commit()
    return {"headers": {"Authorization": f"Bearer {token}"}}


def test_first_sync_generates_insight(client, auth):
    with (
        patch("app.services.snapshot_aggregator.oura.fetch_daily_data", return_value=DAY_V1),
        patch("app.services.insight_generator.anthropic.Anthropic") as mock_anthropic,
    ):
        mock_anthropic.return_value.messages.create.return_value = _fake_claude("Insight v1")
        r = client.post("/api/sync", headers=auth["headers"])

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changed"] is True
    assert body["snapshot"]["steps"] == 6000
    assert body["insight"]["insight_text"] == "Insight v1"
    assert body["insight_error"] is None
    assert mock_anthropic.return_value.messages.create.called


def test_unchanged_poll_skips_claude(client, auth):
    with (
        patch("app.services.snapshot_aggregator.oura.fetch_daily_data", return_value=DAY_V1),
        patch("app.services.insight_generator.anthropic.Anthropic") as mock_anthropic,
    ):
        r = client.post("/api/sync", headers=auth["headers"])

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changed"] is False
    assert body["insight"]["insight_text"] == "Insight v1"  # cached, not regenerated
    assert not mock_anthropic.return_value.messages.create.called


def test_changed_data_regenerates_insight(client, auth):
    with (
        patch("app.services.snapshot_aggregator.oura.fetch_daily_data", return_value=DAY_V2),
        patch("app.services.insight_generator.anthropic.Anthropic") as mock_anthropic,
    ):
        mock_anthropic.return_value.messages.create.return_value = _fake_claude("Insight v2")
        r = client.post("/api/sync", headers=auth["headers"])

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changed"] is True
    assert body["snapshot"]["steps"] == 9500
    assert body["insight"]["insight_text"] == "Insight v2"
    assert mock_anthropic.return_value.messages.create.called


def test_insight_failure_still_returns_snapshot(client, auth):
    day_v3 = {**DAY_V2, "steps": 12000}
    with (
        patch("app.services.snapshot_aggregator.oura.fetch_daily_data", return_value=day_v3),
        patch("app.services.insight_generator.anthropic.Anthropic") as mock_anthropic,
    ):
        mock_anthropic.return_value.messages.create.side_effect = RuntimeError("Claude is down")
        r = client.post("/api/sync", headers=auth["headers"])

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["snapshot"]["steps"] == 12000
    assert body["insight_error"] == "Claude is down"
    # stale insight is still returned so the UI has something to show
    assert body["insight"]["insight_text"] == "Insight v2"
