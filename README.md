# HealthSync

AI-powered health data aggregation. Pulls from wearables and nutrition platforms, normalises everything into one daily snapshot per user, and uses Claude to deliver 2-3 contextual insights a day instead of raw dashboards.

**Current state:** working end-to-end slice — user auth → WHOOP/Oura OAuth → snapshot aggregation → Claude insight generation — plus an iOS test app (`ios/`) that polls `POST /api/sync` every 30 seconds. WHOOP is the primary tested integration; Oura is also implemented. MyFitnessPal, the web frontend, and the cron scheduler are next.

## Stack

- **Backend:** Python / FastAPI + SQLAlchemy 2.0
- **Database:** PostgreSQL (JSONB for sources/flags metadata)
- **AI:** Anthropic Claude API (`claude-sonnet-4-6` by default — the handover's `claude-sonnet-4-20250514` retires 2026-06-15)
- **Auth:** JWT + bcrypt; OAuth tokens encrypted at rest (Fernet)

## Getting started

```bash
cp .env.example .env
# Fill in: JWT_SECRET, TOKEN_ENCRYPTION_KEY, WHOOP_CLIENT_ID/SECRET, ANTHROPIC_API_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # → TOKEN_ENCRYPTION_KEY

docker compose up --build
# API at http://localhost:8000 — interactive docs at /docs
```

Or run locally without Docker:

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload   # needs Postgres running and DATABASE_URL set
```

## End-to-end walkthrough

```bash
# 1. Register (returns a JWT)
TOKEN=$(curl -s -X POST localhost:8000/api/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"a-strong-password"}' | jq -r .access_token)

# 2. Get the WHOOP authorize URL, open it in a browser, approve access.
#    WHOOP redirects to /api/integrations/whoop/callback which stores the tokens.
curl -s localhost:8000/api/integrations/whoop/connect -H "Authorization: Bearer $TOKEN"

# 3. Sync all connected platforms + (re)generate the insight in one call
curl -s -X POST localhost:8000/api/sync -H "Authorization: Bearer $TOKEN"

# 4. Log today's nutrition totals (replace semantics — each POST sets the
#    day's totals; re-POST running totals to update). Regenerates the insight
#    if values changed and returns {snapshot, insight, changed}.
curl -s -X POST localhost:8000/api/nutrition \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"calories": 2450, "protein_g": 168, "carbs_g": 240, "fat_g": 80}'

# 5. Quick read of today's nutrition + active calories (no sync triggered)
curl -s localhost:8000/api/nutrition/today -H "Authorization: Bearer $TOKEN"

# 6. Or fetch today's stored insight + snapshot without syncing
curl -s localhost:8000/api/insights/today -H "Authorization: Bearer $TOKEN"
```

The iOS test app (`ios/` — setup in `ios/README.md`) does all of the above from a phone and repeats step 3 every 30 seconds.

## API surface

| Method | Path | Description |
|---|---|---|
| POST | `/api/auth/register` | Create account, returns JWT |
| POST | `/api/auth/login` | Returns JWT |
| GET | `/api/integrations/{platform}/connect` | OAuth authorize URL for `whoop` or `oura` (auth required) |
| GET | `/api/integrations/{platform}/callback` | OAuth redirect target — stores encrypted tokens |
| POST | `/api/integrations/{platform}/sync?day=YYYY-MM-DD` | Aggregate one platform into the daily snapshot (backfills) |
| POST | `/api/sync` | Sync all connected platforms + regenerate insight if data changed — the iOS app's 30s poll |
| POST | `/api/nutrition` | Log the day's nutrition totals (replace semantics); regenerates insight on change |
| GET | `/api/nutrition/today` | Today's nutrition fields + active calories + BMR constant |
| GET | `/api/insights/today` | Today's insight + raw snapshot; generates on demand if missing |
| GET | `/healthz` | Liveness check |

## Project layout

```
backend/
  app/
    api/            # route handlers (auth, integrations, sync, insights)
    integrations/   # one module per platform (whoop.py, oura.py; mfp.py next)
    models/         # SQLAlchemy models: User, IntegrationToken, DailySnapshot, DailyInsight
    services/       # snapshot_aggregator.py, insight_generator.py
    auth/           # JWT, bcrypt, Fernet token encryption
  main.py
  requirements.txt
ios/                # SwiftUI test app — login, OAuth connect, 30s polling dashboard
```

## Design decisions (from the handover)

- **Self-referenced baselines:** insights compare against the user's own rolling 30-day averages, never population norms. Baseline-relative flags only activate after 14+ days of history; absolute flags (energy balance vs. a fixed 1700 kcal BMR, protein floor of 1.2 g/kg at 75 kg — constants in `app/constants.py`, per-user later) fire from day one.
- **Manual nutrition logging:** no viable third-party nutrition API yet (MFP's is dead), so the iOS app logs day totals directly to `POST /api/nutrition` — same snapshot row, same change-detection path as wearable syncs.
- **Deterministic flags, LLM prose:** anomaly flags (`hrv_drop`, `protein_deficit`, …) are computed in code; Claude writes the plain-language interpretation. Flags stay queryable and consistent.
- **Cheap frequent sync:** `POST /api/sync` is safe to poll every 30s — it re-calls Claude only when platform data actually changed, so no-op polls cost a few platform API calls and zero Claude tokens. Freshness is still bounded by the wearable's own cloud sync (open the WHOOP app to push the strap's latest data to WHOOP's cloud).
- **Performance tool, not medical:** the prompt explicitly forbids medical advice.

## Next up (v2 slice)

1. MyFitnessPal integration (`integrations/mfp.py`) → fills the nutrition columns
2. Scheduled 6am aggregation job (cron / APScheduler / Celery beat)
3. React dashboard: today's insight card + 7-day HRV/calories/sleep trends
4. Apple HealthKit (the iOS app is the natural host for it)
5. Alembic migrations (currently `create_all` on startup)
