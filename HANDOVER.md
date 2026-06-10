# HealthSync — Project Handover

> Status as of 2026-06-10, branch `claude/adoring-hypatia-ajnebs`.
> Picks up from the original product handover (AI health data aggregation: pull from wearables/nutrition platforms, normalise into one daily snapshot per user, have Claude deliver 1-3 contextual insights instead of raw dashboards).

---

## TL;DR

A **working end-to-end MVP slice** exists and is tested:

```
iOS app (SwiftUI) ──30s poll──▶ POST /api/sync
                                   │
                                   ├─▶ WHOOP v2 API (primary, owner has device)
                                   ├─▶ Oura v2 API (implemented, untested with real creds)
                                   │        │
                                   │        ▼
                                   │   daily_snapshot upsert (change detection)
                                   │        │
                                   │        ▼ only if data changed
                                   └─▶ Claude API ──▶ daily_insight
                                            │
                                   {snapshot, insight, flags} ──▶ app dashboard
```

- **Backend:** Python/FastAPI + SQLAlchemy 2.0 + PostgreSQL, JWT auth, OAuth tokens encrypted at rest
- **iOS app:** SwiftUI test client (login → connect WHOOP → live dashboard polling every 30s)
- **Tests:** 17 passing (`backend/tests/`), external APIs mocked
- **Not built yet:** MyFitnessPal, scheduled cron job, React web dashboard, Apple HealthKit, Alembic migrations

---

## Commit history

| Commit | What |
|---|---|
| `f9e9dad` | Backend scaffold: auth, models, Oura integration, snapshot aggregation, Claude insights, docker-compose, tests |
| `b237a65` | iOS app, WHOOP integration (now primary), combined `POST /api/sync` polling endpoint, platform-generic integration routes |

---

## What's implemented

### 1. Auth (`backend/app/auth/`, `backend/app/api/auth.py`)
- Email/password registration + login, bcrypt hashing, JWT bearer tokens (PyJWT, HS256)
- `get_current_user` FastAPI dependency guards all user endpoints
- Short-lived signed JWT used as the OAuth `state` parameter so callbacks can identify the user without server-side sessions (`create_state_token` / `decode_state_token` in `auth/security.py`)
- OAuth access/refresh tokens are **Fernet-encrypted at rest** (`auth/crypto.py`); requires `TOKEN_ENCRYPTION_KEY` env var

### 2. Data model (`backend/app/models/`)
Matches the original handover schema exactly:
- `users` — id (UUID), email, password_hash
- `integration_tokens` — per (user, platform): encrypted access/refresh token, expiry; unique constraint on (user_id, platform)
- `daily_snapshot` — one row per user per day; sleep / recovery / activity / nutrition columns, `sources` JSON (`{"whoop": true}`); unique on (user_id, date)
- `daily_insight` — insight_text + `flags` JSON; unique on (user_id, date)

Tables are created via `Base.metadata.create_all` on app startup — **no Alembic yet**, so schema changes on a live DB need manual handling.

### 3. Integrations (`backend/app/integrations/`)
Common shape per platform module: `build_authorize_url(state)`, `exchange_code(code)`, `refresh_access_token(rt)`, `fetch_daily_data(access_token, day) -> dict` returning normalised snapshot field names. All errors subclass `IntegrationError` (defined in `integrations/__init__.py`).

**WHOOP (`whoop.py`) — primary, owner has the device:**
- OAuth: `api.prod.whoop.com/oauth/oauth2/{auth,token}`; scopes `read:recovery read:cycles read:sleep read:workout offline` — `offline` is required to get a refresh token, and WHOOP requires `scope=offline` again on the refresh call
- Data (v2): `/v2/recovery` (recovery_score, hrv_rmssd_milli, resting_heart_rate), `/v2/activity/sleep` (stage durations in **milliseconds**, non-nap, longest period picked), `/v2/cycle` (strain 0-21, kilojoule → kcal at ÷4.184), `/v2/activity/workout` (summed durations)
- Only records with `score_state == "SCORED"` are used

**Oura (`oura.py`) — implemented, never run against real credentials:**
- OAuth: `cloud.ouraring.com/oauth/authorize` + `api.ouraring.com/oauth/token`
- Data (v2): `/usercollection/sleep` (longest period: duration, efficiency, deep/REM, average_hrv, lowest_heart_rate as resting HR), `/usercollection/daily_readiness` (score → recovery_score), `/usercollection/daily_activity` (steps, active_calories, high+medium activity time → workout_minutes)

### 4. Snapshot aggregation (`backend/app/services/snapshot_aggregator.py`)
- `sync_platform_snapshot(db, user, day, platform)` — refreshes the token if within 5 min of expiry, fetches, upserts into `daily_snapshot`, merges `sources`. Returns `(snapshot, changed)` — **`changed` is the load-bearing bit**: it's what gates Claude regeneration on polls.
- `sync_all_connected(db, user, day)` — syncs every platform the user has linked; one platform failing doesn't stop the others (errors returned per-platform).
- `PLATFORMS` dict stores **module references**, not bound functions — deliberate, so tests can monkey-patch `whoop.fetch_daily_data` and have it take effect.

### 5. Insight generation (`backend/app/services/insight_generator.py`)
- Uses the prompt template from the original handover verbatim (plain-language coach, 3-4 sentences, "Do not give medical advice")
- **Deterministic flags, LLM prose:** anomaly flags (`hrv_drop`, `low_recovery`, `short_sleep`, `protein_deficit`) are computed in code against the user's own rolling 30-day baseline — Claude only writes the narrative. Flags need **14+ days of history** to activate; before that you get `{"baseline_immature": true}`.
- Thresholds: HRV < 85% of avg; recovery/sleep/protein < 80% of avg.
- Claude call: `anthropic.Anthropic()` (key from `ANTHROPIC_API_KEY` env), non-streaming, `max_tokens=1024`. Insights are cached per (user, day); regenerated only with `force=True` (i.e. when sync detected changed data).
- **Model:** defaults to `claude-sonnet-4-6` via `ANTHROPIC_MODEL`. ⚠️ The original handover pinned `claude-sonnet-4-20250514`, which **retires 2026-06-15** — do not switch back to it.

### 6. API surface (`backend/app/api/`)

| Method | Path | Notes |
|---|---|---|
| POST | `/api/auth/register` | 201 + JWT; password ≥ 8 chars |
| POST | `/api/auth/login` | JWT |
| GET | `/api/integrations/{platform}/connect` | `whoop` or `oura`; returns `authorize_url` |
| GET | `/api/integrations/{platform}/callback` | OAuth redirect target; stores encrypted tokens; registered per-platform because each is a whitelisted redirect URI |
| POST | `/api/integrations/{platform}/sync?day=` | Single-platform sync, mainly for backfills |
| **POST** | **`/api/sync`** | **The polling endpoint.** Syncs all connected platforms, regenerates insight only if data changed. Returns `{snapshot, insight, changed, insight_error, source_errors}`. Insight failure does NOT fail the poll — snapshot still returns, error in `insight_error`. 400 if no integrations connected; 502 only if every platform failed. |
| GET | `/api/insights/today` | Stored insight + snapshot; generates on demand; 404 if no snapshot today |
| GET | `/healthz` | Liveness |

### 7. iOS app (`ios/HealthSync/`)
SwiftUI, no third-party dependencies, iOS 17+. Not an `.xcodeproj` — you create a blank SwiftUI app in Xcode and drag the files in (2-minute setup, exact steps in `ios/README.md`).

- `HealthSyncApp.swift` — root; switches Login/Dashboard on stored token (`@AppStorage`)
- `APIClient.swift` — URLSession wrapper; snake_case JSON decoding; auto-logout on 401; 25s request timeout (under the 30s poll)
- `Views/LoginView.swift` — server URL + register/login
- `Views/DashboardView.swift` — insight card, flag chips, metric grid (sleep, efficiency, HRV, resting HR, recovery, **strain**, steps, active kcal, workout); **30s polling loop** via `.task(id: autoSync)` async loop; immediate sync on foreground; pull-to-refresh; ⋯ menu: auto-sync toggle, sync now, Connect WHOOP / Connect Oura, logout
- OAuth from the phone: app fetches `authorize_url`, opens Safari, user approves, WHOOP redirects to the **backend's** callback (not back into the app) — user manually returns to the app, next poll shows data. Crude but fine for a test client.

### 8. Infra
- `docker-compose.yml` — Postgres 16 (healthchecked), Redis 7 (**declared but unused by code so far** — intended for token/response caching later), backend build
- `.env.example` — all required vars with generation instructions
- `backend/Dockerfile` — python:3.12-slim + uvicorn

### 9. Tests (`backend/tests/`, 17 passing)
Run: `cd backend && pip install -r requirements-dev.txt && python -m pytest tests/ -q`
- Runs against throwaway SQLite (env set in `conftest.py` **before** app import); WHOOP/Oura HTTP and Claude are mocked; everything else (routing, auth, encryption, upserts, baselines) is real
- `test_end_to_end.py` — register → Oura callback → sync → insight → caching → baseline math
- `test_sync_endpoint.py` — the change-detection contract: first sync generates, unchanged poll skips Claude, changed data regenerates, insight failure still returns snapshot
- `test_whoop.py` — WHOOP v2 record normalisation (ms→min, kJ→kcal, nap filtering, unscored records ignored), `/api/sync` via WHOOP, unknown-platform 404

---

## Key design decisions (and why)

1. **30s polling instead of the original 6am daily sync** — owner's request for near-instant testing. Made affordable by change detection: a no-op poll costs ~4 platform API calls and **zero Claude calls**. The 6am scheduled job is still the right call for production; the polling endpoint doesn't preclude it.
2. **Freshness is bounded by the wearable's cloud** — the WHOOP strap only uploads via the WHOOP phone app. "Open the WHOOP app to force a strap sync" is part of the test loop; HealthSync picks it up ≤30s after it lands in WHOOP's cloud.
3. **WHOOP promoted over Oura** — owner has a WHOOP. Oura code is complete but has never seen real credentials; treat it as untested.
4. **Deterministic flags + LLM prose split** — flags stay queryable/consistent for future UI and trend logic; Claude never decides what's anomalous.
5. **Self-referenced baselines** (from original handover) — 30-day rolling averages per user, never population norms; 14-day maturity gate.
6. **iOS test app instead of the React dashboard first** — owner wanted to test from a phone. The React web dashboard remains on the roadmap.
7. **UTC "today" everywhere** — `datetime.now(timezone.utc).date()`. Per-user local-time days (the original spec's "6am user local time") are a known simplification to revisit.

---

## How to run

```bash
cp .env.example .env
# Required: JWT_SECRET, TOKEN_ENCRYPTION_KEY, WHOOP_CLIENT_ID/SECRET, ANTHROPIC_API_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # → TOKEN_ENCRYPTION_KEY
docker compose up --build      # API on :8000, interactive docs at /docs
```

iOS: see `ios/README.md`. Curl walkthrough: see root `README.md`.

**Physical-iPhone gotcha:** the phone hits the backend over LAN, so (a) server URL in the app = `http://<Mac-LAN-IP>:8000`, (b) `WHOOP_REDIRECT_URI` in `.env` must use that same LAN IP, and (c) that exact URI must be registered in the WHOOP developer portal. ATS exception (`Allow Local Networking`) needed for plain HTTP.

---

## Known gaps / debt

| Item | Notes |
|---|---|
| Third-party nutrition API | MFP's API is dead; Cronometer/Nutritionix are a later decision. Interim solution shipped: **manual day-totals logging** via `POST /api/nutrition` + iOS quick-log sheet (replace semantics, same snapshot row and change-detection path as wearable syncs). Energy-balance and protein-floor flags use fixed constants (`app/constants.py`: 1700 kcal BMR, 75 kg bodyweight) — make per-user columns later. |
| Scheduled job | No cron/APScheduler. Production should run `sync_all_connected` + `generate_insight` per user at 6am local. |
| React web dashboard | Not started (iOS app covers testing). |
| Apple HealthKit | Not started; the iOS app is the natural host (HealthKit is iOS-SDK-only, no server API). |
| Alembic | `create_all` on startup only; first schema change needs migrations. |
| Redis | In compose but unused. Intended for OAuth token caching / rate-limit management. |
| Oura with real creds | Code complete, tests green, never exercised against the live API. |
| OAuth callback UX | Callback returns bare JSON in Safari; user manually returns to the app. A redirect to a custom URL scheme (`healthsync://connected`) would close the loop. |
| Token storage on device | iOS app keeps the JWT in `UserDefaults`; fine for a test client, move to Keychain for anything real. |
| Multi-platform conflicts | If both WHOOP and Oura are connected, last-synced platform wins on overlapping fields (sleep, HRV...). Fine for single-device users; needs a precedence policy later. |
| `main.py` import path | Backend must run from `backend/` (`uvicorn main:app`); imports are `app.*`-rooted. |

## Suggested next steps (in order)

1. **Test the WHOOP flow with real credentials** — most valuable validation, and the only step requiring nothing new to be built.
2. Scheduled daily job (6am) so insights exist without the app being open.
3. MyFitnessPal (or interim manual nutrition logging endpoint) to light up the nutrition half of the insight story.
4. OAuth callback → app redirect via custom URL scheme.
5. Alembic before any schema change.
6. React dashboard / HealthKit per original roadmap.
