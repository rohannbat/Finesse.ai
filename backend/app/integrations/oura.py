"""Oura v2 API integration: OAuth2 flow + daily data fetch.

Docs: https://cloud.ouraring.com/docs
"""

from datetime import date
from urllib.parse import urlencode

import httpx

from app.config import get_settings
from app.integrations import IntegrationError

AUTHORIZE_URL = "https://cloud.ouraring.com/oauth/authorize"
TOKEN_URL = "https://api.ouraring.com/oauth/token"
API_BASE = "https://api.ouraring.com/v2"

SCOPES = "daily heartrate personal session"


class OuraError(IntegrationError):
    pass


def build_authorize_url(state: str) -> str:
    settings = get_settings()
    params = {
        "response_type": "code",
        "client_id": settings.oura_client_id,
        "redirect_uri": settings.oura_redirect_uri,
        "scope": SCOPES,
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code(code: str) -> dict:
    """Exchange an authorization code for access/refresh tokens."""
    settings = get_settings()
    resp = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.oura_redirect_uri,
            "client_id": settings.oura_client_id,
            "client_secret": settings.oura_client_secret,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise OuraError(f"Token exchange failed ({resp.status_code}): {resp.text}")
    return resp.json()


def refresh_access_token(refresh_token: str) -> dict:
    settings = get_settings()
    resp = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": settings.oura_client_id,
            "client_secret": settings.oura_client_secret,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise OuraError(f"Token refresh failed ({resp.status_code}): {resp.text}")
    return resp.json()


def _get(client: httpx.Client, path: str, params: dict) -> dict:
    resp = client.get(f"{API_BASE}{path}", params=params)
    if resp.status_code == 401:
        raise OuraError("Oura access token rejected (401)")
    if resp.status_code != 200:
        raise OuraError(f"Oura API error on {path} ({resp.status_code}): {resp.text}")
    return resp.json()


def fetch_daily_data(access_token: str, day: date) -> dict:
    """Fetch sleep, readiness, and activity for one day, normalised to the
    daily_snapshot column names. Missing data yields None values."""
    params = {"start_date": day.isoformat(), "end_date": day.isoformat()}
    out: dict = {
        "sleep_duration_min": None,
        "sleep_efficiency_pct": None,
        "deep_sleep_min": None,
        "rem_sleep_min": None,
        "hrv_ms": None,
        "resting_hr": None,
        "recovery_score": None,
        "active_calories": None,
        "steps": None,
        "workout_minutes": None,
    }

    with httpx.Client(headers={"Authorization": f"Bearer {access_token}"}, timeout=30) as client:
        # Detailed sleep periods — pick the main (longest) period for the day
        sleep_data = _get(client, "/usercollection/sleep", params).get("data", [])
        if sleep_data:
            main = max(sleep_data, key=lambda s: s.get("total_sleep_duration") or 0)
            if main.get("total_sleep_duration") is not None:
                out["sleep_duration_min"] = round(main["total_sleep_duration"] / 60)
            out["sleep_efficiency_pct"] = main.get("efficiency")
            if main.get("deep_sleep_duration") is not None:
                out["deep_sleep_min"] = round(main["deep_sleep_duration"] / 60)
            if main.get("rem_sleep_duration") is not None:
                out["rem_sleep_min"] = round(main["rem_sleep_duration"] / 60)
            out["hrv_ms"] = main.get("average_hrv")
            out["resting_hr"] = main.get("lowest_heart_rate")

        # Readiness score → normalised recovery_score (already 0-100)
        readiness = _get(client, "/usercollection/daily_readiness", params).get("data", [])
        if readiness:
            out["recovery_score"] = readiness[0].get("score")

        # Activity
        activity = _get(client, "/usercollection/daily_activity", params).get("data", [])
        if activity:
            a = activity[0]
            out["active_calories"] = a.get("active_calories")
            out["steps"] = a.get("steps")
            high = a.get("high_activity_time") or 0
            medium = a.get("medium_activity_time") or 0
            if high or medium:
                out["workout_minutes"] = round((high + medium) / 60)

    return out
