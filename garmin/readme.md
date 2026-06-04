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
| `garmin_sync.py` | Main sync script |
| `login_once.py` | One-time interactive auth, run manually |
| `garmin_token_health.py` | Token expiry check + session validation |
| `dockerfile` | Container definition |
| `requirements.txt` | Python deps: `garminconnect`, `psycopg2-binary`, `python-dotenv` |

---

## Caveats

- Body composition fields are null if no weigh-in was recorded that day
- Some metrics depend on device support (e.g. SpO2, endurance score)
- The sync script auto-adapts to existing DB columns — new columns require a schema migration first
- Garmin's unofficial API is used — no partner approval required but may break on Garmin API changes

---

## Git safety

Do not commit: `.env`, `tokens/`, `garmin_tokens.json`
