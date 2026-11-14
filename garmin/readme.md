# Garmin Sync Service

Pulls daily health, recovery, and body composition metrics from Garmin Connect into PostgreSQL.

---

## What it syncs

| Category | Fields |
|----------|--------|
| Daily | Steps, calories, resting HR |
| Sleep | Duration, score, deep/light/REM/awake breakdown, user notes |
| Recovery | HRV, body battery, training readiness, stress |
| Training load | Weekly load, acute/chronic load, ACWR, training status, balance feedback |
| Physiology | VO2 max, endurance score, heat acclimation, respiration, SpO2 |
| Body composition | Weight, body fat %, BMI, muscle mass, bone mass, body water (Garmin Index scale) |

Writes to `garmin_daily` using `ON CONFLICT (date) DO UPDATE` — safe to re-run.

---

## Data source: `GARMIN_SOURCE`

| Value | Behavior |
|---|---|
| `connect` (default) | Everything via unofficial `garminconnect`, as before. |
| `official` | Garmin **Personal API** first (`GARMIN_PERSONAL_KEY`); `garminconnect` fills what the API doesn't expose. |

In `official` mode:
- **Personal API:** daily summary (steps, kcal, RHR, stress, body battery, respiration, SpO2, intensity minutes), sleep summary, HRV, weight/body comp, VO2 max, activities + FIT/GPS, HR and HRV intraday.
- **garminconnect (gap-fill):** training readiness/status/load/ACWR/balance, recovery time, endurance, heat acclimation, skin temp, activity/sedentary seconds, BB at wake, sleep notes, sleep/stress/BB/steps/respiration intraday, per-activity HR zone floors, FTP, race predictions. Min/max HR and floors also come from garminconnect when available (different definitions; keeps history continuous).
- If the **garminconnect login fails**, the official data still syncs; gap columns are left untouched (not nulled) and a WARNING goes to stderr.
- If the **Personal API token fails**, it falls back to full `connect` mode.
- Personal API rate limit: 100 requests per rolling window (429 + `retryInSeconds`). The client caches per run and retries; a 3-day run uses ~30 calls.
- Key expires (30/90/180/365 days): regenerate at connect.garmin.com > Settings > Personal API Key and update `.env`, then recreate the container (`docker compose up -d`) so it picks up the new env.

Verified 2026-11-14 with dry runs over 7 days: `official` and `connect` produce identical rows for activities, FIT/GPS and all intraday tables; `garmin_daily` differs only in `body_battery` (official uses Garmin's "most recent value"; connect used the last sampled point — 1 of 7 days differed). Background: `experiments/garmin-personal-api/FINDINGS.md` on the `spike/garmin-personal-api` branch.

### Dry run

```bash
docker exec -e GARMIN_DRY_RUN=1 -e GARMIN_SOURCE=official garmin-sync python garmin_sync.py
```

No DB writes, webhooks or token saves; recorded writes go to `GARMIN_DRY_RUN_OUT` (default `/tmp/garmin_dry_run_<source>.json`). Compare two runs with `python tools/compare_dry_runs.py a.json b.json`.

---

## Authentication

Uses the `garminconnect` Python library with stored DI tokens.

### First-time setup

```bash
docker exec -it garmin-sync python login_once.py
```

You will be prompted for your Garmin email, password, and MFA code. Tokens are saved to the `GARMINTOKENS` directory and reused on subsequent runs.

### Token lifecycle

- `di_token` — 27-hour access token, auto-refreshed by `garminconnect` on every `login()` call
- `di_refresh_token` — long-lived (likely 90+ days), used to refresh the access token
- If the refresh token expires, re-run `login_once.py` manually with MFA

### Token health check

```bash
docker exec garmin-sync python garmin_token_health.py
```

Runs every 6 hours via the Sync Staleness Watchdog in n8n. Alerts via ntfy if token is expired or session is invalid.

---

## Configuration

```env
GARMIN_EMAIL=your_email
GARMIN_PASSWORD=your_password
GARMINTOKENS=/root/.garminconnect

DB_HOST=10.10.0.10
DB_PORT=5432
DB_NAME=postgres
DB_USER=postgres
DB_PASSWORD=your_password

GARMIN_LOOKBACK_DAYS=3

# Optional: official Personal API as primary source
GARMIN_SOURCE=official
GARMIN_PERSONAL_KEY=your_personal_api_key
```

Tokens are mounted via Docker volume:
```yaml
volumes:
  - /mnt/user/appdata/fitness-sync/garmin/tokens:/root/.garminconnect
```

---

## Schedule

Runs hourly via n8n Container Sync Schedules. Also runs on iOS Shortcut trigger and after deploy.

---

## Files

| File | Purpose |
|------|---------|
| `garmin_sync.py` | Main sync script (source selection, dry run) |
| `services/official.py` | Garmin Personal API client |
| `services/daily.py` | `garmin_daily`: connect blocks, official parser, merge |
| `tools/compare_dry_runs.py` | Row-by-row diff of two dry-run outputs |
| `login_once.py` | One-time interactive auth, run manually |
| `garmin_token_health.py` | Token expiry check + session validation |
| `dockerfile` | Container definition |
| `requirements.txt` | Python deps: `garminconnect`, `psycopg2-binary`, `python-dotenv` |

---

## Caveats

- Body composition fields are null if no weigh-in was recorded that day
- Some metrics depend on device support (e.g. SpO2, endurance score)
- The sync script auto-adapts to existing DB columns — new columns require a schema migration first
- In `connect` mode Garmin's unofficial API is used — may break on Garmin API changes. `official` mode limits that exposure to the gap-fill fields.

---

## Git safety

Do not commit: `.env`, `tokens/`, `garmin_tokens.json`
