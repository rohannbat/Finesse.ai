"""Generate the daily plain-language insight via the Claude API."""

from datetime import date, timedelta

import anthropic
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import DailyInsight, DailySnapshot, User

PROMPT_TEMPLATE = """You are a personal health performance coach. Given today's health data and the user's 30-day baseline, generate 2-3 plain-language observations and one actionable recommendation. Be specific, not generic. Do not give medical advice.

Today's data:
- Sleep: {sleep_duration_min} min, {sleep_efficiency_pct}% efficient, HRV {hrv_ms}ms
- Recovery score: {recovery_score}/100
- Calories consumed: {calories_consumed} kcal, Protein: {protein_g}g
- Active calories burned: {active_calories} kcal, Steps: {steps}
- Workout: {workout_minutes} min

30-day averages (this user):
- Avg sleep: {avg_sleep} min, Avg HRV: {avg_hrv}ms
- Avg recovery: {avg_recovery}, Avg protein: {avg_protein}g

Respond in 3-4 sentences. Focus on what's notable today compared to their normal."""


def _fmt(value, digits: int = 0) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}" if digits else f"{value:.1f}"
    return str(value)


def compute_baseline(db: Session, user_id, day: date) -> dict:
    """Rolling 30-day averages for this user, excluding the target day."""
    start = day - timedelta(days=30)
    row = db.execute(
        select(
            func.avg(DailySnapshot.sleep_duration_min),
            func.avg(DailySnapshot.hrv_ms),
            func.avg(DailySnapshot.recovery_score),
            func.avg(DailySnapshot.protein_g),
            func.count(DailySnapshot.id),
        ).where(
            DailySnapshot.user_id == user_id,
            DailySnapshot.date >= start,
            DailySnapshot.date < day,
        )
    ).one()
    return {
        "avg_sleep": float(row[0]) if row[0] is not None else None,
        "avg_hrv": float(row[1]) if row[1] is not None else None,
        "avg_recovery": float(row[2]) if row[2] is not None else None,
        "avg_protein": float(row[3]) if row[3] is not None else None,
        "days_of_data": int(row[4]),
    }


def compute_flags(snapshot: DailySnapshot, baseline: dict) -> dict:
    """Deterministic anomaly flags vs the user's own baseline.

    Only flagged once there's 14+ days of history, per the personalisation
    design decision (compare to self, not population).
    """
    flags: dict = {}
    if baseline["days_of_data"] < 14:
        flags["baseline_immature"] = True
        return flags

    if snapshot.hrv_ms is not None and baseline["avg_hrv"]:
        if snapshot.hrv_ms < baseline["avg_hrv"] * 0.85:
            flags["hrv_drop"] = True
    if snapshot.recovery_score is not None and baseline["avg_recovery"]:
        if snapshot.recovery_score < baseline["avg_recovery"] * 0.8:
            flags["low_recovery"] = True
    if snapshot.sleep_duration_min is not None and baseline["avg_sleep"]:
        if snapshot.sleep_duration_min < baseline["avg_sleep"] * 0.8:
            flags["short_sleep"] = True
    if snapshot.protein_g is not None and baseline["avg_protein"]:
        if snapshot.protein_g < baseline["avg_protein"] * 0.8:
            flags["protein_deficit"] = True
    return flags


def build_prompt(snapshot: DailySnapshot, baseline: dict) -> str:
    return PROMPT_TEMPLATE.format(
        sleep_duration_min=_fmt(snapshot.sleep_duration_min),
        sleep_efficiency_pct=_fmt(snapshot.sleep_efficiency_pct),
        hrv_ms=_fmt(snapshot.hrv_ms),
        recovery_score=_fmt(snapshot.recovery_score),
        calories_consumed=_fmt(snapshot.calories_consumed),
        protein_g=_fmt(snapshot.protein_g),
        active_calories=_fmt(snapshot.active_calories),
        steps=_fmt(snapshot.steps),
        workout_minutes=_fmt(snapshot.workout_minutes),
        avg_sleep=_fmt(baseline["avg_sleep"]),
        avg_hrv=_fmt(baseline["avg_hrv"]),
        avg_recovery=_fmt(baseline["avg_recovery"]),
        avg_protein=_fmt(baseline["avg_protein"]),
    )


def generate_insight(db: Session, user: User, day: date, force: bool = False) -> DailyInsight:
    """Generate (or return the existing) insight for a user's day."""
    existing = db.scalar(
        select(DailyInsight).where(DailyInsight.user_id == user.id, DailyInsight.date == day)
    )
    if existing is not None and not force:
        return existing

    snapshot = db.scalar(
        select(DailySnapshot).where(DailySnapshot.user_id == user.id, DailySnapshot.date == day)
    )
    if snapshot is None:
        raise ValueError(f"No snapshot for {day} — sync integrations first")

    baseline = compute_baseline(db, user.id, day)
    flags = compute_flags(snapshot, baseline)
    prompt = build_prompt(snapshot, baseline)

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    response = client.messages.create(
        model=get_settings().anthropic_model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    insight_text = next((b.text for b in response.content if b.type == "text"), "").strip()
    if not insight_text:
        raise RuntimeError(f"Claude returned no text (stop_reason={response.stop_reason})")

    if existing is not None:
        existing.insight_text = insight_text
        existing.flags = flags
        insight = existing
    else:
        insight = DailyInsight(user_id=user.id, date=day, insight_text=insight_text, flags=flags)
        db.add(insight)

    db.commit()
    db.refresh(insight)
    return insight
