"""End-to-end slice test: auth → Oura connect/callback → sync → insight.

External calls (Oura HTTP, Claude API) are mocked; everything else — routing,
auth, encryption at rest, upserts, baseline/flag math — runs for real against
a throwaway SQLite database.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.auth.crypto import decrypt_token
from app.auth.security import create_state_token
from app.database import SessionLocal
from app.models import DailySnapshot, IntegrationToken, User
from app.services.insight_generator import compute_baseline, compute_flags

FAKE_OURA_DAY = {
    "sleep_duration_min": 432,
    "sleep_efficiency_pct": 91.0,
    "deep_sleep_min": 80,
    "rem_sleep_min": 95,
    "hrv_ms": 48.0,
    "resting_hr": 52,
    "recovery_score": 74.0,
    "active_calories": 540,
    "steps": 11200,
    "workout_minutes": 35,
}


@pytest.fixture(scope="module")
def auth(client):
    email = f"test-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/auth/register", json={"email": email, "password": "longpassword1"})
    assert r.status_code == 201, r.text
    token = r.json()["access_token"]
    with SessionLocal() as db:
        user_id = db.query(User).filter_by(email=email).one().id
    return {"email": email, "headers": {"Authorization": f"Bearer {token}"}, "user_id": user_id}


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_login_and_bad_password(client, auth):
    assert client.post("/api/auth/login", json={"email": auth["email"], "password": "longpassword1"}).status_code == 200
    assert client.post("/api/auth/login", json={"email": auth["email"], "password": "wrongpass99"}).status_code == 401


def test_requires_auth(client):
    assert client.get("/api/insights/today").status_code == 401


def test_oura_connect_url(client, auth):
    r = client.get("/api/integrations/oura/connect", headers=auth["headers"])
    assert r.status_code == 200
    assert "cloud.ouraring.com/oauth/authorize" in r.json()["authorize_url"]


def test_oura_callback_stores_encrypted_tokens(client, auth):
    state = create_state_token(str(auth["user_id"]), "oura")
    with patch(
        "app.api.integrations.oura.exchange_code",
        return_value={"access_token": "oura-access", "refresh_token": "oura-refresh", "expires_in": 86400},
    ):
        r = client.get("/api/integrations/oura/callback", params={"code": "fake", "state": state})
    assert r.status_code == 200 and r.json()["status"] == "connected", r.text

    with SessionLocal() as db:
        row = db.query(IntegrationToken).filter_by(user_id=auth["user_id"], platform="oura").one()
        assert row.access_token != "oura-access"
        assert decrypt_token(row.access_token) == "oura-access"


def test_sync_upserts_snapshot(client, auth):
    with patch("app.services.snapshot_aggregator.oura.fetch_daily_data", return_value=FAKE_OURA_DAY):
        r = client.post("/api/integrations/oura/sync", headers=auth["headers"])
    assert r.status_code == 200, r.text
    snap = r.json()
    assert snap["hrv_ms"] == 48.0
    assert snap["sources"] == {"oura": True}

    with patch("app.services.snapshot_aggregator.oura.fetch_daily_data", return_value=FAKE_OURA_DAY):
        r2 = client.post("/api/integrations/oura/sync", headers=auth["headers"])
    assert r2.json()["id"] == snap["id"]  # same row, not a duplicate


def test_insight_generation_and_caching(client, auth):
    fake_block = MagicMock()
    fake_block.type = "text"
    fake_block.text = "Your HRV of 48ms is right at your normal. Recovery is solid."
    fake_resp = MagicMock(content=[fake_block], stop_reason="end_turn")

    with patch("app.services.insight_generator.anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value.messages.create.return_value = fake_resp
        r = client.get("/api/insights/today", headers=auth["headers"])
        assert r.status_code == 200, r.text
        body = r.json()
        assert "HRV" in body["insight"]["insight_text"]
        assert body["insight"]["flags"] == {"baseline_immature": True}
        assert body["snapshot"]["steps"] == 11200

        call = mock_anthropic.return_value.messages.create.call_args.kwargs
        prompt = call["messages"][0]["content"]
        assert "Sleep: 432 min" in prompt
        assert "HRV 48.0ms" in prompt
        assert "Do not give medical advice" in prompt

        # second request returns the stored insight without another API call
        mock_anthropic.return_value.messages.create.reset_mock()
        assert client.get("/api/insights/today", headers=auth["headers"]).status_code == 200
        assert not mock_anthropic.return_value.messages.create.called


def test_baseline_and_flags(auth):
    today = datetime.now(timezone.utc).date()
    with SessionLocal() as db:
        for i in range(1, 21):
            db.add(
                DailySnapshot(
                    user_id=auth["user_id"],
                    date=today - timedelta(days=i),
                    hrv_ms=60.0,
                    recovery_score=80.0,
                    sleep_duration_min=450,
                    sources={"oura": True},
                )
            )
        db.commit()

        baseline = compute_baseline(db, auth["user_id"], today)
        assert baseline["days_of_data"] == 20
        assert baseline["avg_hrv"] == pytest.approx(60.0)

        low_day = DailySnapshot(
            user_id=auth["user_id"],
            date=today,
            hrv_ms=45.0,
            recovery_score=78.0,
            sleep_duration_min=440,
            sources={"oura": True},
        )
        assert compute_flags(low_day, baseline) == {"hrv_drop": True}
