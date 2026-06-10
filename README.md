# HealthSync

A calorie logging app that digests WHOOP data. Log what you eat meal by meal, sync recovery/strain/sleep from your WHOOP, and get AI insights two ways: an auto-generated daily insight, and a conversational **AI coach** (WHOOP-Coach-style chat) grounded in your actual numbers.

**Current state:** working end to end — user auth → WHOOP OAuth → snapshot aggregation → meal-level food logging → Claude daily insight + coach chat — with an iOS app (`ios/`, Today + Coach tabs) that polls `POST /api/sync` every 30 seconds. Oura is also implemented (untested with real creds). Web frontend and the cron scheduler are next.

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

# 4. Log a meal (entries sum into the day's totals; insight regenerates
#    when totals change). Returns {entries, snapshot, insight, changed}.
curl -s -X POST localhost:8000/api/food \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name": "Chicken and rice", "calories": 650, "protein_g": 48}'

# 5. List today's entries / delete one (totals recompute)
curl -s localhost:8000/api/food -H "Authorization: Bearer $TOKEN"
curl -s -X DELETE localhost:8000/api/food/<entry-id> -H "Authorization: Bearer $TOKEN"

# 6. Ask the AI coach (grounded in your WHOOP + food data)
curl -s -X POST localhost:8000/api/coach/chat \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"message": "Should I train hard today?"}'

# 7. Quick reads
curl -s localhost:8000/api/nutrition/today -H "Authorization: Bearer $TOKEN"
curl -s localhost:8000/api/insights/today -H "Authorization: Bearer $TOKEN"
curl -s localhost:8000/api/coach/history -H "Authorization: Bearer $TOKEN"
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
| POST | `/api/food` | Log one food/meal; day totals recompute from all entries; insight regenerates on change |
| GET | `/api/food?day=` | List a day's food entries |
| DELETE | `/api/food/{id}` | Delete an entry; totals recompute |
| POST | `/api/coach/chat` | Ask the AI coach — Claude answers grounded in today + 7-day + baseline data |
| GET | `/api/coach/history` | Coach conversation history |
| DELETE | `/api/coach/history` | Clear the conversation |
| GET | `/api/nutrition/today` | Today's nutrition totals + active calories + BMR constant |
| GET | `/api/insights/today` | Today's insight + raw snapshot; generates on demand if missing |
| GET | `/healthz` | Liveness check |

## Project layout

```
backend/
  app/
    api/            # route handlers (auth, integrations, sync, food, coach, insights, nutrition)
    integrations/   # one module per platform (whoop.py, oura.py)
    models/         # User, IntegrationToken, DailySnapshot, DailyInsight, FoodEntry, CoachMessage
    services/       # snapshot_aggregator, nutrition_service, insight_generator, coach
    auth/           # JWT, bcrypt, Fernet token encryption
  main.py
  requirements.txt
ios/                # SwiftUI app — Today tab (dashboard + food log) and Coach tab (AI chat)
```

## Design decisions (from the handover)

- **Self-referenced baselines:** insights compare against the user's own rolling 30-day averages, never population norms. Baseline-relative flags only activate after 14+ days of history; absolute flags (energy balance vs. a fixed 1700 kcal BMR, protein floor of 1.2 g/kg at 75 kg — constants in `app/constants.py`, per-user later) fire from day one.
- **Meal-level food logging:** no viable third-party nutrition API (MFP's is dead), so entries are logged in-app via `POST /api/food` and the day's totals in `daily_snapshot` are recomputed as the sum of entries — same snapshot row, same change-detection path as wearable syncs.
- **Two AI surfaces, one data source:** the daily insight (auto-generated, cached, regenerates only on data change) and the coach chat (`/api/coach/chat`, WHOOP-Coach-style). Every coach turn rebuilds a context block — today's snapshot + food log + last 7 days + 30-day baseline + active flags — into the system prompt, so answers cite the user's real numbers.
- **Deterministic flags, LLM prose:** anomaly flags (`hrv_drop`, `protein_deficit`, …) are computed in code; Claude writes the plain-language interpretation. Flags stay queryable and consistent.
- **Cheap frequent sync:** `POST /api/sync` is safe to poll every 30s — it re-calls Claude only when platform data actually changed, so no-op polls cost a few platform API calls and zero Claude tokens. Freshness is still bounded by the wearable's own cloud sync (open the WHOOP app to push the strap's latest data to WHOOP's cloud).
- **Performance tool, not medical:** the prompt explicitly forbids medical advice.

## Next up (v2 slice)

1. Scheduled 6am aggregation job (cron / APScheduler / Celery beat)
2. Food database / barcode lookup so entries don't need manual macro input
3. Coach streaming responses (SSE) for a snappier chat feel
4. Apple HealthKit (the iOS app is the natural host for it)
5. Alembic migrations (currently `create_all` on startup)
6. React dashboard per the original roadmap
