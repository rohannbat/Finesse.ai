"""Food-entry logging tests: totals rollup, wearable-field preservation,
delete/clear behaviour, validation, and the insight regeneration contract."""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.constants import BMR_KCAL
from app.database import SessionLocal
from app.models import DailySnapshot, User


def _fake_claude(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    return MagicMock(content=[block], stop_reason="end_turn")


@pytest.fixture(scope="module")
def auth(client):
    email = f"meals-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/auth/register", json={"email": email, "password": "longpassword1"})
    assert r.status_code == 201, r.text
    with SessionLocal() as db:
        user_id = db.query(User).filter_by(email=email).one().id
    return {"headers": {"Authorization": f"Bearer {r.json()['access_token']}"}, "user_id": user_id}


def test_first_entry_creates_snapshot(client, auth):
    with patch("app.services.insight_generator.anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value.messages.create.return_value = _fake_claude("Meal note v1")
        r = client.post(
            "/api/food",
            headers=auth["headers"],
            json={"name": "Oats with whey", "calories": 420, "protein_g": 32, "carbs_g": 60, "fat_g": 9},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changed"] is True
    assert len(body["entries"]) == 1
    assert body["entries"][0]["name"] == "Oats with whey"
    assert body["snapshot"]["calories_consumed"] == 420
    assert body["snapshot"]["protein_g"] == 32
    assert body["snapshot"]["sources"] == {"manual": True}
    assert body["insight"]["insight_text"] == "Meal note v1"
    assert body["bmr_kcal"] == BMR_KCAL


def test_second_entry_accumulates_totals(client, auth):
    with patch("app.services.insight_generator.anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value.messages.create.return_value = _fake_claude("Meal note v2")
        r = client.post(
            "/api/food",
            headers=auth["headers"],
            json={"name": "Chicken and rice", "calories": 650, "protein_g": 48},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["entries"]) == 2
    assert body["snapshot"]["calories_consumed"] == 1070  # 420 + 650
    assert body["snapshot"]["protein_g"] == 80  # 32 + 48
    assert body["snapshot"]["carbs_g"] == 60  # only the first entry had carbs
    assert body["changed"] is True
    assert mock_anthropic.return_value.messages.create.called


def test_entries_preserve_wearable_fields(client, auth):
    today = datetime.now(timezone.utc).date()
    with SessionLocal() as db:
        snapshot = db.query(DailySnapshot).filter_by(user_id=auth["user_id"], date=today).one()
        snapshot.hrv_ms = 52.3
        snapshot.strain_score = 14.2
        snapshot.sources = {**snapshot.sources, "whoop": True}
        db.commit()

    with patch("app.services.insight_generator.anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value.messages.create.return_value = _fake_claude("Meal note v3")
        r = client.post(
            "/api/food",
            headers=auth["headers"],
            json={"name": "Greek yoghurt", "calories": 150, "protein_g": 15},
        )
    snap = r.json()["snapshot"]
    assert snap["calories_consumed"] == 1220
    assert snap["hrv_ms"] == 52.3  # wearable data untouched
    assert snap["sources"] == {"manual": True, "whoop": True}


def test_list_food(client, auth):
    r = client.get("/api/food", headers=auth["headers"])
    assert r.status_code == 200
    body = r.json()
    assert [e["name"] for e in body["entries"]] == ["Oats with whey", "Chicken and rice", "Greek yoghurt"]


def test_delete_entry_recomputes_totals(client, auth):
    entries = client.get("/api/food", headers=auth["headers"]).json()["entries"]
    yoghurt = next(e for e in entries if e["name"] == "Greek yoghurt")

    with patch("app.services.insight_generator.anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value.messages.create.return_value = _fake_claude("Meal note v4")
        r = client.delete(f"/api/food/{yoghurt['id']}", headers=auth["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["entries"]) == 2
    assert body["snapshot"]["calories_consumed"] == 1070
    assert body["changed"] is True


def test_delete_last_entries_clears_nutrition(client, auth):
    with patch("app.services.insight_generator.anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value.messages.create.return_value = _fake_claude("Meal note v5")
        for entry in client.get("/api/food", headers=auth["headers"]).json()["entries"]:
            r = client.delete(f"/api/food/{entry['id']}", headers=auth["headers"])
            assert r.status_code == 200
    body = r.json()
    assert body["entries"] == []
    assert body["snapshot"]["calories_consumed"] is None
    assert "manual" not in body["snapshot"]["sources"]
    assert body["snapshot"]["sources"] == {"whoop": True}  # wearable source survives


def test_delete_unknown_entry_404(client, auth):
    r = client.delete(f"/api/food/{uuid.uuid4()}", headers=auth["headers"])
    assert r.status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "Bad", "calories": -100},
        {"name": "Bad", "calories": 9000},
        {"name": "Bad", "calories": 400, "protein_g": -5},
        {"name": "Bad", "calories": 400, "protein_g": 900},
        {"name": "", "calories": 400},
        {"calories": 400},  # name missing
        {"name": "Bad", "calories": "lots"},
    ],
)
def test_validation_rejections(client, auth, payload):
    r = client.post("/api/food", headers=auth["headers"], json=payload)
    assert r.status_code == 422, f"{payload} -> {r.status_code}"


def test_nutrition_today_reflects_entries(client, auth):
    with patch("app.services.insight_generator.anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value.messages.create.return_value = _fake_claude("Meal note v6")
        client.post(
            "/api/food",
            headers=auth["headers"],
            json={"name": "Salmon bowl", "calories": 700, "protein_g": 45},
        )
    r = client.get("/api/nutrition/today", headers=auth["headers"])
    assert r.status_code == 200
    body = r.json()
    assert body["calories_consumed"] == 700
    assert body["protein_g"] == 45
    assert body["bmr_kcal"] == BMR_KCAL
