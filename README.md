# HealthSync

AI-powered health data aggregation. Pulls from wearables and nutrition platforms, normalises everything into one daily snapshot per user, and uses Claude to deliver 2-3 contextual insights a day instead of raw dashboards.

**Current state:** working end-to-end backend slice — user auth → Oura OAuth → daily snapshot aggregation → Claude insight generation → `GET /api/insights/today`. Frontend, MyFitnessPal, WHOOP, and the cron scheduler are next.

## Stack

- **Backend:** Python / FastAPI + SQLAlchemy 2.0
- **Database:** PostgreSQL (JSONB for sources/flags metadata)
- **AI:** Anthropic Claude API (`claude-sonnet-4-6` by default — the handover's `claude-sonnet-4-20250514` retires 2026-06-15)
- **Auth:** JWT + bcrypt; OAuth tokens encrypted at rest (Fernet)

## Getting started

```bash
cp .env.example .env
# Fill in: JWT_SECRET, TOKEN_ENCRYPTION_KEY, OURA_CLIENT_ID/SECRET, ANTHROPIC_API_KEY
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

# 2. Get the Oura authorize URL, open it in a browser, approve access.
#    Oura redirects to /api/integrations/oura/callback which stores the tokens.
curl -s localhost:8000/api/integrations/oura/connect -H "Authorization: Bearer $TOKEN"

# 3. Pull today's Oura data into the normalised snapshot
curl -s -X POST localhost:8000/api/integrations/oura/sync -H "Authorization: Bearer $TOKEN"

# 4. Get today's AI insight + raw snapshot
curl -s localhost:8000/api/insights/today -H "Authorization: Bearer $TOKEN"
```

## API surface

| Method | Path | Description |
|---|---|---|
| POST | `/api/auth/register` | Create account, returns JWT |
| POST | `/api/auth/login` | Returns JWT |
| GET | `/api/integrations/oura/connect` | Returns Oura OAuth authorize URL (auth required) |
| GET | `/api/integrations/oura/callback` | OAuth redirect target — stores encrypted tokens |
| POST | `/api/integrations/oura/sync?day=YYYY-MM-DD` | Aggregate Oura data into the daily snapshot (defaults to today) |
| GET | `/api/insights/today` | Today's insight + raw snapshot; generates on demand if missing |
| GET | `/healthz` | Liveness check |

## Project layout

```
backend/
  app/
    api/            # route handlers (auth, integrations, insights)
    integrations/   # one module per platform (oura.py; mfp.py next)
    models/         # SQLAlchemy models: User, IntegrationToken, DailySnapshot, DailyInsight
    services/       # snapshot_aggregator.py, insight_generator.py
    auth/           # JWT, bcrypt, Fernet token encryption
  main.py
  requirements.txt
```

## Design decisions (from the handover)

- **Self-referenced baselines:** insights compare against the user's own rolling 30-day averages, never population norms. Anomaly flags only activate after 14+ days of history.
- **Deterministic flags, LLM prose:** anomaly flags (`hrv_drop`, `protein_deficit`, …) are computed in code; Claude writes the plain-language interpretation. Flags stay queryable and consistent.
- **Daily sync, not real-time:** integrations sync once per day. `/api/integrations/oura/sync` is the manual trigger; wire it to a 6am-local cron/scheduler for production.
- **Performance tool, not medical:** the prompt explicitly forbids medical advice.

## Next up (v2 slice)

1. MyFitnessPal integration (`integrations/mfp.py`) → fills the nutrition columns
2. Scheduled 6am aggregation job (cron / APScheduler / Celery beat)
3. React dashboard: today's insight card + 7-day HRV/calories/sleep trends
4. WHOOP + Apple HealthKit
5. Alembic migrations (currently `create_all` on startup)
