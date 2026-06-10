"""WHOOP v2 API integration: OAuth2 flow + daily data fetch.

Docs: https://developer.whoop.com
"""

from datetime import date, datetime, timedelta
from urllib.parse import urlencode

import httpx

from app.config import get_settings
from app.integrations import IntegrationError

AUTHORIZE_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
API_BASE = "https://api.prod.whoop.com/developer"

# `offline` is required to receive a refresh token
SCOPES = "read:recovery read:cycles read:sleep read:workout offline"

KCAL_PER_KILOJOULE = 1 / 4.184


class WhoopError(IntegrationError):
    pass


def build_authorize_url(state: str) -> str:
    settings = get_settings()
    params = {
        "response_type": "code",
        "client_id": settings.whoop_client_id,
        "redirect_uri": settings.whoop_redirect_uri,
        "scope": SCOPES,
        "state": state,  # WHOOP requires state to be 8+ chars; ours is a JWT
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code(code: str) -> dict:
    settings = get_settings()
    resp = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.whoop_redirect_uri,
            "client_id": settings.whoop_client_id,
            "client_secret": settings.whoop_client_secret,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise WhoopError(f"Token exchange failed ({resp.status_code}): {resp.text}")
    return resp.json()


def refresh_access_token(refresh_token: str) -> dict:
    settings = get_settings()
    resp = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": settings.whoop_client_id,
            "client_secret": settings.whoop_client_secret,
            "scope": "offline",  # WHOOP requires re-requesting offline on refresh
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise WhoopError(f"Token refresh failed ({resp.status_code}): {resp.text}")
    return resp.json()


def _get_records(client: httpx.Client, path: str, params: dict) -> list[dict]:
    resp = client.get(f"{API_BASE}{path}", params=params)
    if resp.status_code == 401:
        raise WhoopError("WHOOP access token rejected (401)")
    if resp.status_code != 200:
        raise WhoopError(f"WHOOP API error on {path} ({resp.status_code}): {resp.text}")
    return resp.json().get("records", [])


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def fetch_daily_data(access_token: str, day: date) -> dict:
    """Fetch recovery, sleep, cycle (strain), and workouts for one day,
    normalised to the daily_snapshot column names."""
    start = f"{day.isoformat()}T00:00:00.000Z"
    end = f"{(day + timedelta(days=1)).isoformat()}T00:00:00.000Z"
    params = {"start": start, "end": end, "limit": 25}

    out: dict = {
        "sleep_duration_min": None,
        "sleep_efficiency_pct": None,
        "deep_sleep_min": None,
        "rem_sleep_min": None,
        "hrv_ms": None,
        "resting_hr": None,
        "recovery_score": None,
        "strain_score": None,
        "active_calories": None,
        "workout_minutes": None,
    }

    with httpx.Client(headers={"Authorization": f"Bearer {access_token}"}, timeout=30) as client:
        # Recovery → recovery_score (0-100), HRV, resting HR
        recoveries = [
            r for r in _get_records(client, "/v2/recovery", params)
            if (r.get("score_state") == "SCORED" and r.get("score"))
        ]
        if recoveries:
            score = recoveries[0]["score"]
            out["recovery_score"] = score.get("recovery_score")
            out["hrv_ms"] = score.get("hrv_rmssd_milli")
            out["resting_hr"] = score.get("resting_heart_rate")

        # Sleep — main (non-nap) sleep, stage durations are in milliseconds
        sleeps = [
            s for s in _get_records(client, "/v2/activity/sleep", params)
            if (s.get("score_state") == "SCORED" and s.get("score") and not s.get("nap"))
        ]
        if sleeps:
            def _total_sleep_ms(s: dict) -> int:
                stages = s["score"].get("stage_summary", {})
                return (
                    (stages.get("total_light_sleep_time_milli") or 0)
                    + (stages.get("total_slow_wave_sleep_time_milli") or 0)
                    + (stages.get("total_rem_sleep_time_milli") or 0)
                )

            main = max(sleeps, key=_total_sleep_ms)
            stages = main["score"].get("stage_summary", {})
            total_ms = _total_sleep_ms(main)
            if total_ms:
                out["sleep_duration_min"] = round(total_ms / 60000)
            if stages.get("total_slow_wave_sleep_time_milli") is not None:
                out["deep_sleep_min"] = round(stages["total_slow_wave_sleep_time_milli"] / 60000)
            if stages.get("total_rem_sleep_time_milli") is not None:
                out["rem_sleep_min"] = round(stages["total_rem_sleep_time_milli"] / 60000)
            out["sleep_efficiency_pct"] = main["score"].get("sleep_efficiency_percentage")

        # Cycle → strain (0-21) and energy burned (kilojoules)
        cycles = [
            c for c in _get_records(client, "/v2/cycle", params)
            if (c.get("score_state") == "SCORED" and c.get("score"))
        ]
        if cycles:
            score = cycles[0]["score"]
            out["strain_score"] = score.get("strain")
            if score.get("kilojoule") is not None:
                out["active_calories"] = round(score["kilojoule"] * KCAL_PER_KILOJOULE)

        # Workouts → total minutes
        workouts = _get_records(client, "/v2/activity/workout", params)
        total_minutes = 0
        for w in workouts:
            if w.get("start") and w.get("end"):
                total_minutes += (_parse_ts(w["end"]) - _parse_ts(w["start"])).total_seconds() / 60
        if total_minutes:
            out["workout_minutes"] = round(total_minutes)

    return out
