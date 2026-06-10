"""WHOOP integration tests: v2 record normalisation + /api/sync end-to-end."""

import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.auth.crypto import encrypt_token
from app.database import SessionLocal
from app.integrations import whoop
from app.models import IntegrationToken, User

WHOOP_RECOVERY = [{
    "score_state": "SCORED",
    "score": {"recovery_score": 67.0, "hrv_rmssd_milli": 52.3, "resting_heart_rate": 54},
}]
WHOOP_SLEEP = [{
    "score_state": "SCORED",
    "nap": False,
    "score": {
        "sleep_efficiency_percentage": 89.5,
        "stage_summary": {
            "total_light_sleep_time_milli": 14_400_000,   # 240 min
            "total_slow_wave_sleep_time_milli": 4_800_000,  # 80 min
            "total_rem_sleep_time_milli": 5_400_000,        # 90 min
        },
    },
}]
WHOOP_CYCLE = [{
    "score_state": "SCORED",
    "score": {"strain": 14.2, "kilojoule": 2092.0},  # ≈ 500 kcal
}]
WHOOP_WORKOUTS = [{
    "start": "2026-06-10T07:00:00.000Z",
    "end": "2026-06-10T07:45:00.000Z",
}]


def test_fetch_daily_data_normalisation():
    def fake_get_records(client, path, params):
        return {
            "/v2/recovery": WHOOP_RECOVERY,
            "/v2/activity/sleep": WHOOP_SLEEP,
            "/v2/cycle": WHOOP_CYCLE,
            "/v2/activity/workout": WHOOP_WORKOUTS,
        }[path]

    with patch("app.integrations.whoop._get_records", side_effect=fake_get_records):
        data = whoop.fetch_daily_data("fake-token", date(2026, 6, 10))

    assert data["recovery_score"] == 67.0
    assert data["hrv_ms"] == 52.3
    assert data["resting_hr"] == 54
    assert data["sleep_duration_min"] == 410  # 240 + 80 + 90
    assert data["deep_sleep_min"] == 80
    assert data["rem_sleep_min"] == 90
    assert data["sleep_efficiency_pct"] == 89.5
    assert data["strain_score"] == 14.2
    assert data["active_calories"] == 500
    assert data["workout_minutes"] == 45


def test_unscored_records_are_ignored():
    def fake_get_records(client, path, params):
        if path == "/v2/recovery":
            return [{"score_state": "PENDING_SCORE", "score": None}]
        return []

    with patch("app.integrations.whoop._get_records", side_effect=fake_get_records):
        data = whoop.fetch_daily_data("fake-token", date(2026, 6, 10))

    assert data["recovery_score"] is None
    assert data["hrv_ms"] is None


@pytest.fixture(scope="module")
def whoop_auth(client):
    email = f"whoop-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/auth/register", json={"email": email, "password": "longpassword1"})
    assert r.status_code == 201, r.text
    token = r.json()["access_token"]
    with SessionLocal() as db:
        user = db.query(User).filter_by(email=email).one()
        db.add(
            IntegrationToken(
                user_id=user.id,
                platform="whoop",
                access_token=encrypt_token("whoop-access"),
                refresh_token=encrypt_token("whoop-refresh"),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=12),
            )
        )
        db.commit()
    return {"headers": {"Authorization": f"Bearer {token}"}}


def test_whoop_connect_url(client, whoop_auth):
    r = client.get("/api/integrations/whoop/connect", headers=whoop_auth["headers"])
    assert r.status_code == 200
    url = r.json()["authorize_url"]
    assert "api.prod.whoop.com/oauth/oauth2/auth" in url
    assert "read%3Arecovery" in url and "offline" in url


def test_sync_endpoint_uses_whoop(client, whoop_auth):
    fake_day = {
        "sleep_duration_min": 410,
        "sleep_efficiency_pct": 89.5,
        "deep_sleep_min": 80,
        "rem_sleep_min": 90,
        "hrv_ms": 52.3,
        "resting_hr": 54,
        "recovery_score": 67.0,
        "strain_score": 14.2,
        "active_calories": 500,
        "workout_minutes": 45,
    }
    fake_block = MagicMock()
    fake_block.type = "text"
    fake_block.text = "Strain of 14.2 against a 67% recovery is a balanced day."
    fake_resp = MagicMock(content=[fake_block], stop_reason="end_turn")

    with (
        patch("app.services.snapshot_aggregator.whoop.fetch_daily_data", return_value=fake_day),
        patch("app.services.insight_generator.anthropic.Anthropic") as mock_anthropic,
    ):
        mock_anthropic.return_value.messages.create.return_value = fake_resp
        r = client.post("/api/sync", headers=whoop_auth["headers"])

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changed"] is True
    assert body["snapshot"]["strain_score"] == 14.2
    assert body["snapshot"]["sources"] == {"whoop": True}
    assert body["source_errors"] == {}
    assert "Strain" in body["insight"]["insight_text"]


def test_unknown_platform_404(client, whoop_auth):
    assert client.get("/api/integrations/fitbit/connect", headers=whoop_auth["headers"]).status_code == 404
