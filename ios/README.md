# HealthSync iOS

A SwiftUI calorie-logging app fed by WHOOP data, with two tabs:

- **Today** — live dashboard (insight card, metric grid, food log with per-meal entries) polling `POST /api/sync` every 30 seconds
- **Coach** — WHOOP-Coach-style AI chat grounded in your actual WHOOP + nutrition data

No third-party dependencies — just drop the files into an Xcode project.

## Setup (~2 minutes)

1. In Xcode: **File → New → Project → iOS → App**
   - Product name: `HealthSync`
   - Interface: SwiftUI, Language: Swift
2. Delete the generated `ContentView.swift` and the generated `HealthSyncApp.swift`.
3. Drag the contents of `ios/HealthSync/` (all `.swift` files, including `Views/`) into the project navigator — check **"Copy items if needed"**.
4. Allow plain-HTTP traffic to your dev backend. In the target's **Info** tab, add:
   - `App Transport Security Settings` (dictionary)
     - `Allow Local Networking` = `YES`
   - If your backend runs on a non-local host over HTTP, use `Allow Arbitrary Loads` = `YES` instead (test builds only — never ship that).
5. Build & run (iOS 17+ deployment target).

## Pointing it at the backend

| Where the app runs | Server URL to enter on the login screen |
|---|---|
| iOS Simulator (same Mac as backend) | `http://localhost:8000` |
| Physical iPhone (same Wi-Fi) | `http://<your-Mac-LAN-IP>:8000` (e.g. `http://192.168.1.20:8000`) |

Start the backend first: `docker compose up --build` from the repo root.

For a **physical device**, also point the WHOOP redirect URI at your LAN IP in `.env` (`WHOOP_REDIRECT_URI=http://<LAN-IP>:8000/api/integrations/whoop/callback`) and register that exact URI in your WHOOP developer app — otherwise the OAuth callback from Safari on the phone can't reach the backend.

## Using it

1. **Create account** on the login screen (any email + 8-char password).
2. Menu (⋯) → **Connect WHOOP** — approve in Safari; the backend callback page shows `{"status": "connected"}`. Return to the app.
3. The Today tab polls every 30s (toggle in the ⋯ menu, or pull-to-refresh / "Sync now" for manual).
4. **Add food** (Food log section) — name + calories per meal, macros optional. Entries sum into the day's totals; the Calories in / Energy balance / Protein tiles update immediately and the insight regenerates when totals change. Tap ✕ on an entry to delete it (totals recompute).
5. **Coach tab** — ask anything about your data ("Should I train hard today?", "How's my protein this week?"). Claude answers from your live WHOOP + food numbers; the conversation persists across sessions.

## How the 30-second sync works

- The app calls `POST /api/sync` every 30s while foregrounded (and once on returning to foreground). Polling pauses in the background — iOS suspends timers anyway, and the next poll catches up.
- The backend syncs **every connected platform** (WHOOP and/or Oura), then **only calls Claude when the data actually changed** — no-op polls cost a few platform API calls and zero Claude tokens.
- End-to-end freshness is bounded by the wearable's own pipeline: the WHOOP strap uploads to WHOOP's cloud via the WHOOP phone app. Open the WHOOP app to force a strap sync; HealthSync picks up the new data within 30s of it landing in WHOOP's cloud.
