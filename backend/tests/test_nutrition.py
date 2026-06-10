"""Nutrition logging tests: upsert semantics, validation, change-detection
contract with insight regeneration, and the new energy-balance/protein flags."""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.constants import BMR_KCAL
from app.database import SessionLocal
from app.models import DailySnapshot, User
from app.services.insight_generator import build_prompt, compute_flags

IMMATURE_BASELINE = {
    "avg_sleep": None,
    "avg_hrv": None,
    "avg_recovery": None,
    "avg_protein": None,
    "days_of_data": 0,
}


def _fake_claude(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    return MagicMock(content=[block], stop_reason="end_turn")


@pytest.fixture(scope="module")
def auth(client):
    email = f"food-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/auth/register", json={"email": email, "password": "longpassword1"})
    assert r.status_code == 201, r.text
    with SessionLocal() as db:
        user_id = db.query(User).filter_by(email=email).one().id
    return {"headers": {"Authorization": f"Bearer {r.json()['access_token']}"}, "user_id": user_id}


def test_post_creates_row_when_no_sync_yet(client, auth):
    with patch("app.services.insight_generator.anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value.messages.create.return_value = _fake_claude("Fueling note v1")
        r = client.post(
            "/api/nutrition",
            headers=auth["headers"],
            json={"calories": 2450, "protein_g": 168, "carbs_g": 240, "fat_g": 80},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changed"] is True
    assert body["snapshot"]["calories_consumed"] == 2450
    assert body["snapshot"]["protein_g"] == 168
    assert body["snapshot"]["sources"] == {"manual": True}
    assert body["insight"]["insight_text"] == "Fueling note v1"
    assert body["bmr_kcal"] == BMR_KCAL


def test_identical_repost_does_not_regenerate(client, auth):
    with patch("app.services.insight_generator.anthropic.Anthropic") as mock_anthropic:
        r = client.post(
            "/api/nutrition",
            headers=auth["headers"],
            json={"calories": 2450, "protein_g": 168, "carbs_g": 240, "fat_g": 80},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changed"] is False
    assert body["insight"]["insight_text"] == "Fueling note v1"  # cached
    assert not mock_anthropic.return_value.messages.create.called


def test_replace_semantics_and_regeneration(client, auth):
    with patch("app.services.insight_generator.anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value.messages.create.return_value = _fake_claude("Fueling note v2")
        r = client.post(
            "/api/nutrition",
            headers=auth["headers"],
            json={"calories": 2800, "protein_g": 190},  # carbs/fat omitted
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changed"] is True
    assert body["snapshot"]["calories_consumed"] == 2800  # replaced, not summed
    assert body["snapshot"]["protein_g"] == 190
    assert body["snapshot"]["carbs_g"] == 240  # omitted optionals left unchanged
    assert body["insight"]["insight_text"] == "Fueling note v2"
    assert mock_anthropic.return_value.messages.create.called


def test_upsert_preserves_wearable_fields(client, auth):
    today = datetime.now(timezone.utc).date()
    with SessionLocal() as db:
        snapshot = (
            db.query(DailySnapshot).filter_by(user_id=auth["user_id"], date=today).one()
        )
        snapshot.hrv_ms = 52.3
        snapshot.recovery_score = 67.0
        snapshot.strain_score = 14.2
        snapshot.sources = {**snapshot.sources, "whoop": True}
        db.commit()

    with patch("app.services.insight_generator.anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value.messages.create.return_value = _fake_claude("Fueling note v3")
        r = client.post(
            "/api/nutrition",
            headers=auth["headers"],
            json={"calories": 3000, "protein_g": 200},
        )
    assert r.status_code == 200, r.text
    snap = r.json()["snapshot"]
    assert snap["calories_consumed"] == 3000
    assert snap["hrv_ms"] == 52.3  # wearable data untouched
    assert snap["strain_score"] == 14.2
    assert snap["sources"] == {"manual": True, "whoop": True}


@pytest.mark.parametrize(
    "payload",
    [
        {"calories": -100, "protein_g": 150},
        {"calories": 20000, "protein_g": 150},
        {"calories": 2400, "protein_g": -5},
        {"calories": 2400, "protein_g": 1500},
        {"calories": 2400, "protein_g": 150, "carbs_g": -1},
        {"calories": 2400, "protein_g": 150, "fat_g": 5000},
        {"calories": "lots", "protein_g": 150},
        {"protein_g": 150},  # calories missing
    ],
)
def test_validation_rejections(client, auth, payload):
    r = client.post("/api/nutrition", headers=auth["headers"], json=payload)
    assert r.status_code == 422, f"{payload} -> {r.status_code}"


def test_get_nutrition_today(client, auth):
    r = client.get("/api/nutrition/today", headers=auth["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["calories_consumed"] == 3000
    assert body["protein_g"] == 200
    assert body["bmr_kcal"] == BMR_KCAL
    assert "active_calories" in body


def test_get_nutrition_today_empty_for_new_user(client):
    email = f"empty-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/auth/register", json={"email": email, "password": "longpassword1"})
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = client.get("/api/nutrition/today", headers=headers)
    assert r.status_code == 200
    assert r.json()["calories_consumed"] is None


# --- Flag logic (pure functions, no HTTP) ---


def _snapshot(**kwargs) -> DailySnapshot:
    return DailySnapshot(user_id=uuid.uuid4(), date=datetime.now(timezone.utc).date(), **kwargs)


def test_calorie_surplus_flag():
    snap = _snapshot(calories_consumed=3000, active_calories=500)  # 3000 - 2200 = +800
    flags = compute_flags(snap, IMMATURE_BASELINE)
    assert flags["calorie_surplus"] is True
    assert "calorie_deficit" not in flags


def test_calorie_deficit_flag_fires_despite_immature_baseline():
    snap = _snapshot(calories_consumed=1200, active_calories=500)  # 1200 - 2200 = -1000
    flags = compute_flags(snap, IMMATURE_BASELINE)
    assert flags["calorie_deficit"] is True
    assert flags["baseline_immature"] is True  # absolute flag coexists with the gate


def test_balanced_day_no_calorie_flag():
    snap = _snapshot(calories_consumed=2400, active_calories=500)  # 2400 - 2200 = +200
    flags = compute_flags(snap, IMMATURE_BASELINE)
    assert "calorie_surplus" not in flags and "calorie_deficit" not in flags


def test_missing_active_calories_uses_bmr_only():
    snap = _snapshot(calories_consumed=2100, active_calories=None)  # 2100 - 1700 = +400
    flags = compute_flags(snap, IMMATURE_BASELINE)
    assert flags["calorie_surplus"] is True


def test_absolute_protein_floor():
    # 1.2 g/kg * 75 kg = 90 g floor — fires even with no baseline history
    snap = _snapshot(protein_g=80.0)
    assert compute_flags(snap, IMMATURE_BASELINE)["protein_deficit"] is True
    snap = _snapshot(protein_g=95.0)
    assert "protein_deficit" not in compute_flags(snap, IMMATURE_BASELINE)


def test_no_nutrition_no_nutrition_flags():
    snap = _snapshot(hrv_ms=50.0)
    flags = compute_flags(snap, IMMATURE_BASELINE)
    assert flags == {"baseline_immature": True}


def test_prompt_renders_not_logged_for_missing_nutrition():
    snap = _snapshot(sleep_duration_min=420, hrv_ms=50.0)
    prompt = build_prompt(snap, IMMATURE_BASELINE)
    assert "Calories consumed: not logged" in prompt
    assert "Protein: not logged" in prompt
    assert "not loggedg" not in prompt  # unit suffix only when a value exists
    assert "None" not in prompt
    assert "energy balance" in prompt  # the new nutrition instruction


def test_prompt_renders_units_when_nutrition_logged():
    snap = _snapshot(calories_consumed=2450, protein_g=168.0)
    prompt = build_prompt(snap, IMMATURE_BASELINE)
    assert "Calories consumed: 2450 kcal" in prompt
    assert "Protein: 168.0g" in prompt
