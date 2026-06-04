# Strava Sync Service

Syncs Strava activities and power streams into PostgreSQL. Also handles uploading enhanced FIT files from Garmin to Strava, replacing the generic auto-upload with enriched workout data.

---

## What it does

### `strava_sync.py`
Pulls recent Strava activities and stores them in `strava_activities`. For rides, also fetches time-series power/HR/cadence streams and computes best-effort power intervals.

| Table | Contents |
|-------|----------|
| `strava_activities` | Activity metadata: name, type, distance, HR, power, elapsed time |
| `strava_activity_streams` | Raw time-series stream data |
| `activity_streams` | Normalized stream rows with timestamps |
| `activity_best_efforts` | Best 5s / 1min / 5min / 20min power per activity |

### `strava_replace_activity.py`
Auto-syncs Garmin activities to Strava via FIT upload. Replaces the old Garmin → Strava auto-push (now disabled) with an enriched FIT that includes Hevy set data.

**Auto mode** (default, run hourly):
- Queries Garmin for activities in the last 3 days
- Checks DB for what's already on Strava
- Downloads FIT and uploads anything missing
- Retries if FIT is too small (hevy2garmin may not have run yet)
- Treats duplicates as success, not errors

**Manual mode:**
```bash
docker exec strava-sync python strava_replace_activity.py --garmin-id 23119214174
```

---

## Authentication

### Strava OAuth
Tokens stored in `STRAVA_TOKENS_FILE` (mounted at `/data/strava_tokens.json`).

Initial token setup — run the OAuth flow once:
```
https://www.strava.com/oauth/authorize?client_id=CLIENT_ID&response_type=code&redirect_uri=http://localhost&scope=read,activity:read_all,activity:write&approval_prompt=force
```
Exchange the code:
```bash
curl -X POST https://www.strava.com/oauth/token \
  -d client_id=CLIENT_ID \
  -d client_secret=CLIENT_SECRET \
  -d code=AUTH_CODE \
  -d grant_type=authorization_code
```
Save the response to `strava_tokens.json`. Tokens auto-refresh on each run.

Required scopes: `read`, `activity:read_all`, `activity:write`

### Garmin tokens
The strava-sync container also mounts the Garmin token directory for FIT downloads:
```yaml
volumes:
  - /mnt/user/appdata/fitness-sync/strava/tokens:/data
  - /mnt/user/appdata/fitness-sync/garmin/tokens:/root/.garminconnect
```

---

## Configuration

```env
STRAVA_CLIENT_ID=your_client_id
STRAVA_CLIENT_SECRET=your_client_secret
STRAVA_TOKENS_FILE=/data/strava_tokens.json

GARMINTOKENS=/root/.garminconnect

DB_HOST=10.10.0.10
DB_PORT=5432
DB_NAME=postgres
DB_USER=postgres
DB_PASSWORD=your_password

STRAVA_LOOKBACK_DAYS=3
```

---

## Strava Webhook

A Strava webhook subscription fires on every new activity upload. The n8n **Strava Webhook Handler** workflow receives it and immediately runs `strava_sync.py` — no waiting for the hourly schedule.

Subscription registered at: `https://n8n.mayfairlabs.cloud/webhook/strava-webhook`
Subscription ID: `351462`

The hourly strava-sync remains as a catch-up fallback.

---

## Schedule

- `strava_sync.py` — runs as part of the 30-min hevy2garmin chain and hourly catch-up
- `strava_replace_activity.py` — runs every 30 min after hevy2garmin, also on iOS Shortcut trigger
- Also triggered immediately via Strava webhook on new activity

---

## Files

| File | Purpose |
|------|---------|
| `strava_sync.py` | Main activity + stream sync |
| `strava_replace_activity.py` | FIT upload to Strava (auto + manual mode) |
| `backfill_streams.py` | One-time backfill for historical stream data |
| `debug_fit.py` | Debug utility for inspecting FIT file contents |
| `Dockerfile` | Container definition |
| `requirements.txt` | Python deps |

---

## Git safety

Do not commit: `.env`, `tokens/`, `strava_tokens.json`
