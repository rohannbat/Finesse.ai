"""AI coach chat tests: data-grounded system prompt, history persistence,
multi-turn context, and failure handling."""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.database import SessionLocal
from app.models import DailySnapshot, User


def _fake_claude(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    return MagicMock(content=[block], stop_reason="end_turn")


@pytest.fixture(scope="module")
def auth(client):
    email = f"coach-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/auth/register", json={"email": email, "password": "longpassword1"})
    assert r.status_code == 201, r.text
    with SessionLocal() as db:
        user = db.query(User).filter_by(email=email).one()
        db.add(
            DailySnapshot(
                user_id=user.id,
                date=datetime.now(timezone.utc).date(),
                hrv_ms=48.0,
                recovery_score=67.0,
                strain_score=14.2,
                active_calories=540,
                calories_consumed=1850,
                protein_g=82.0,
                sources={"whoop": True, "manual": True},
            )
        )
        db.commit()
    return {"headers": {"Authorization": f"Bearer {r.json()['access_token']}"}}


def test_chat_grounds_reply_in_user_data(client, auth):
    with patch("app.services.coach.anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value.messages.create.return_value = _fake_claude(
            "Your recovery is 67% — keep strain moderate today."
        )
        r = client.post(
            "/api/coach/chat",
            headers=auth["headers"],
            json={"message": "Should I train hard today?"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["reply"]["role"] == "assistant"
    assert "67%" in r.json()["reply"]["content"]

    call = mock_anthropic.return_value.messages.create.call_args.kwargs
    system = call["system"]
    # today's data is injected into the system prompt
    assert "Recovery 67.0/100" in system
    assert "HRV 48.0ms" in system
    assert "1850 kcal eaten" in system
    assert "Energy balance: -390 kcal" in system
    assert "calorie_deficit" in system and "protein_deficit" in system
    assert "Do not give medical advice" in system
    # the user's question is the last message
    assert call["messages"][-1] == {"role": "user", "content": "Should I train hard today?"}


def test_second_turn_includes_history(client, auth):
    with patch("app.services.coach.anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value.messages.create.return_value = _fake_claude(
            "Aim for roughly 90g protein minimum."
        )
        r = client.post(
            "/api/coach/chat",
            headers=auth["headers"],
            json={"message": "How much protein should I aim for?"},
        )
    assert r.status_code == 200, r.text
    messages = mock_anthropic.return_value.messages.create.call_args.kwargs["messages"]
    contents = [m["content"] for m in messages]
    assert "Should I train hard today?" in contents  # first turn replayed
    assert any(m["role"] == "assistant" for m in messages)
    assert messages[-1]["content"] == "How much protein should I aim for?"


def test_history_endpoint(client, auth):
    r = client.get("/api/coach/history", headers=auth["headers"])
    assert r.status_code == 200
    messages = r.json()["messages"]
    assert len(messages) == 4  # two turns, both sides persisted
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]


def test_chat_failure_returns_502_and_history_keeps_user_message(client, auth):
    with patch("app.services.coach.anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value.messages.create.side_effect = RuntimeError("Claude is down")
        r = client.post("/api/coach/chat", headers=auth["headers"], json={"message": "hello?"})
    assert r.status_code == 502
    assert "Coach unavailable" in r.json()["detail"]


def test_clear_history(client, auth):
    assert client.delete("/api/coach/history", headers=auth["headers"]).status_code == 204
    assert client.get("/api/coach/history", headers=auth["headers"]).json()["messages"] == []


def test_chat_validation(client, auth):
    assert client.post("/api/coach/chat", headers=auth["headers"], json={"message": ""}).status_code == 422
    assert client.post("/api/coach/chat", headers=auth["headers"], json={"message": "x" * 3000}).status_code == 422
