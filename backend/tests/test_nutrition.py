"""Nutrition flag logic and prompt rendering (pure functions, no HTTP).

Endpoint coverage for food logging lives in test_food.py.
"""

import uuid
from datetime import datetime, timezone

from app.models import DailySnapshot
from app.services.insight_generator import build_prompt, compute_flags

IMMATURE_BASELINE = {
    "avg_sleep": None,
    "avg_hrv": None,
    "avg_recovery": None,
    "avg_protein": None,
    "days_of_data": 0,
}


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
    assert "energy balance" in prompt  # the nutrition instruction


def test_prompt_renders_units_when_nutrition_logged():
    snap = _snapshot(calories_consumed=2450, protein_g=168.0)
    prompt = build_prompt(snap, IMMATURE_BASELINE)
    assert "Calories consumed: 2450 kcal" in prompt
    assert "Protein: 168.0g" in prompt
