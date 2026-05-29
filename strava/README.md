# Strava Sync Service

This service syncs activity data (rides, power, time-series streams) from Strava into Postgres for analysis and coaching.

It is part of a larger **fitness data platform** combining:

- Garmin (recovery + physiology)
- Strava (cycling performance)
- Hevy (strength training)
- Nutrition tracking
- Claude MCP (analysis + coaching)

---

## What this service does

On each run, `strava_sync.py`:

### Activity data
- Activity name, type, date
- Distance
- Moving time / elapsed time
- Avg/max heart rate
- Avg / max / weighted (NP) power
- Kilojoules (work)

### Stream data (high value)
Stored in:

```
strava_activity_streams
```

Streams include:
- time
- distance
- power (watts)

These are used to build:
- best 5-minute power
- best 20-minute power
- simple power curve
- ride intensity analysis

---

## Authentication

Uses Strava OAuth.

Tokens stored in:

```
strava/tokens/strava_tokens.json
Directory
```

Tokens are:
- required for API access
- NOT committed to Git

---

## Configuration

Create:

```
strava/.env
```

Example:

```env
STRAVA_CLIENT_ID=your_client_id
STRAVA_CLIENT_SECRET=your_secret
STRAVA_REFRESH_TOKEN=your_refresh_token

DB_HOST=your_db_host
DB_PORT=5432
DB_NAME=postgres
DB_USER=postgres
DB_PASSWORD=your_password
```

---

## Database tables

### `strava_activities`
Core activity metadata.

### `strava_activity_streams`
Time-series data:
- `(strava_activity_id, stream_type, idx)` should be UNIQUE

Example constraint:

```sql
alter table strava_activity_streams
add constraint strava_activity_streams_unique
unique (strava_activity_id, stream_type, idx);
```

---

## Derived analytics (views)

These should exist:

- `strava_activity_stream_points`
- `strava_activity_best_efforts`
- `strava_power_curve_simple`

They provide:
- best 5m / 20m power
- power curve
- ride structure analysis

---

## Running locally

```bash
python strava_sync.py
```

---

## Docker usage

```bash
docker build -t strava-sync:latest .
docker run --env-file .env strava-sync:latest
```

---

## Common issues

### No stream data
- Ensure activity has power data
- Ensure correct API scopes

### ON CONFLICT errors
- Missing unique constraint on streams table

### Missing power
- Some rides may not include watts data

---

## Project usage

This feeds into:
- power analytics (FTP estimation, endurance trends)
- fatigue modeling
- Claude coaching insights

---

## Git safety

Do NOT commit:

- `.env`
- `tokens/`
- `strava_tokens.json`
