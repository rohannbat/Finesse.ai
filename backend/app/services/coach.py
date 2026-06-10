"""Conversational AI coach (WHOOP-Coach-style) grounded in the user's data.

Every turn rebuilds a data context block (today + last 7 days + 30-day
baseline + today's food log) into the system prompt, sends the stored
conversation history to Claude, and persists both sides of the exchange.
"""

from datetime import datetime, timedelta, timezone

import anthropic
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.constants import BMR_KCAL
from app.models import CoachMessage, DailySnapshot, FoodEntry, User
from app.services.insight_generator import compute_baseline, compute_flags

# Conversation turns sent to Claude per request (user+assistant messages).
HISTORY_LIMIT = 20

COACH_SYSTEM_TEMPLATE = """You are HealthSync Coach, the user's personal health and nutrition coach inside the HealthSync app — conversational, like the coach feature in a fitness wearable's app.

Rules:
- Ground every answer in the user's actual data below. Cite their numbers ("your HRV is 48ms vs your 60ms norm"), never invent values.
- If data is missing or says "n/a"/"not logged", say so rather than guessing.
- Be direct and specific, not generic. Plain language, no jargon walls.
- Keep answers to 2-5 sentences unless the user asks for detail.
- You may suggest training, food, and sleep adjustments for performance. Do not give medical advice; for symptoms or medical concerns, recommend a professional.
- The user's energy balance is computed against a fixed estimate: BMR {bmr} kcal + active calories burned.

== USER DATA (auto-refreshed each message) ==
{context}
== END USER DATA =="""


def _n(value, suffix: str = "") -> str:
    return "n/a" if value is None else f"{value}{suffix}"


def _day_line(s: DailySnapshot) -> str:
    return (
        f"- {s.date.isoformat()}: recovery {_n(s.recovery_score)}, HRV {_n(s.hrv_ms, 'ms')}, "
        f"sleep {_n(s.sleep_duration_min, 'min')}, strain {_n(s.strain_score)}, "
        f"active {_n(s.active_calories, 'kcal')}, eaten {_n(s.calories_consumed, 'kcal')}, "
        f"protein {_n(s.protein_g, 'g')}"
    )


def build_context(db: Session, user: User) -> str:
    today = datetime.now(timezone.utc).date()
    lines: list[str] = []
    baseline = compute_baseline(db, user.id, today)

    snapshot = db.scalar(
        select(DailySnapshot).where(DailySnapshot.user_id == user.id, DailySnapshot.date == today)
    )
    lines.append(f"Today ({today.isoformat()}):")
    if snapshot is None:
        lines.append("- No data synced or logged yet today.")
    else:
        lines.append(
            f"- Recovery {_n(snapshot.recovery_score)}/100, HRV {_n(snapshot.hrv_ms, 'ms')}, "
            f"resting HR {_n(snapshot.resting_hr)}"
        )
        lines.append(
            f"- Sleep {_n(snapshot.sleep_duration_min, ' min')} "
            f"({_n(snapshot.sleep_efficiency_pct, '%')} efficiency), "
            f"deep {_n(snapshot.deep_sleep_min, ' min')}, REM {_n(snapshot.rem_sleep_min, ' min')}"
        )
        lines.append(
            f"- Strain {_n(snapshot.strain_score)}, active calories {_n(snapshot.active_calories)}, "
            f"workout {_n(snapshot.workout_minutes, ' min')}, steps {_n(snapshot.steps)}"
        )
        lines.append(
            f"- Nutrition: {_n(snapshot.calories_consumed, ' kcal')} eaten, "
            f"protein {_n(snapshot.protein_g, 'g')}, carbs {_n(snapshot.carbs_g, 'g')}, "
            f"fat {_n(snapshot.fat_g, 'g')}"
        )
        if snapshot.calories_consumed is not None:
            balance = snapshot.calories_consumed - ((snapshot.active_calories or 0) + BMR_KCAL)
            lines.append(f"- Energy balance: {balance:+d} kcal")
        flags = compute_flags(snapshot, baseline)
        active_flags = sorted(k for k, v in flags.items() if v)
        if active_flags:
            lines.append(f"- Flags: {', '.join(active_flags)}")

    entries = db.scalars(
        select(FoodEntry)
        .where(FoodEntry.user_id == user.id, FoodEntry.date == today)
        .order_by(FoodEntry.created_at)
    ).all()
    lines.append("Food log today:")
    if entries:
        for e in entries:
            macros = ", ".join(
                f"{_n(v, 'g')} {k}"
                for k, v in (("protein", e.protein_g), ("carbs", e.carbs_g), ("fat", e.fat_g))
                if v is not None
            )
            lines.append(f"- {e.name}: {e.calories} kcal" + (f" ({macros})" if macros else ""))
    else:
        lines.append("- Nothing logged yet.")

    week = db.scalars(
        select(DailySnapshot)
        .where(
            DailySnapshot.user_id == user.id,
            DailySnapshot.date >= today - timedelta(days=7),
            DailySnapshot.date < today,
        )
        .order_by(DailySnapshot.date)
    ).all()
    lines.append("Last 7 days:")
    if week:
        lines.extend(_day_line(s) for s in week)
    else:
        lines.append("- No history yet.")

    lines.append(
        f"30-day baseline ({baseline['days_of_data']} days of data): "
        f"sleep {_n(baseline['avg_sleep'] and round(baseline['avg_sleep']), ' min')}, "
        f"HRV {_n(baseline['avg_hrv'] and round(baseline['avg_hrv'], 1), 'ms')}, "
        f"recovery {_n(baseline['avg_recovery'] and round(baseline['avg_recovery']))}, "
        f"protein {_n(baseline['avg_protein'] and round(baseline['avg_protein']), 'g')}"
    )
    return "\n".join(lines)


def chat(db: Session, user: User, message: str) -> CoachMessage:
    """One coach turn: persist the user message, call Claude with the data
    context + recent history, persist and return the reply."""
    history = db.scalars(
        select(CoachMessage)
        .where(CoachMessage.user_id == user.id)
        .order_by(CoachMessage.created_at.desc())
        .limit(HISTORY_LIMIT)
    ).all()
    history = list(reversed(history))

    # Explicit microsecond timestamps — DB-side CURRENT_TIMESTAMP can have
    # 1s resolution, which makes ordering of a chat turn's two messages
    # non-deterministic.
    user_msg = CoachMessage(
        user_id=user.id, role="user", content=message,
        created_at=datetime.now(timezone.utc),
    )
    db.add(user_msg)
    db.commit()

    system = COACH_SYSTEM_TEMPLATE.format(bmr=BMR_KCAL, context=build_context(db, user))
    messages = [{"role": m.role, "content": m.content} for m in history]
    messages.append({"role": "user", "content": message})

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=get_settings().anthropic_model,
        max_tokens=1024,
        system=system,
        messages=messages,
    )
    reply_text = next((b.text for b in response.content if b.type == "text"), "").strip()
    if not reply_text:
        raise RuntimeError(f"Claude returned no text (stop_reason={response.stop_reason})")

    reply = CoachMessage(
        user_id=user.id, role="assistant", content=reply_text,
        created_at=datetime.now(timezone.utc),
    )
    db.add(reply)
    db.commit()
    db.refresh(reply)
    return reply
