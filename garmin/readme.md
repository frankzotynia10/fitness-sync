# Garmin Sync Service

This service pulls daily health, recovery, and body composition data from Garmin Connect and writes it into Postgres for downstream analysis.

It is part of a larger **fitness data pipeline** that integrates:

- Garmin (recovery + biometrics)
- Strava (cycling / power data)
- Hevy (strength training)
- Nutrition tracking
- Claude MCP (analysis + coaching)

---

## What this service does

On each run, `garmin_sync.py`:

### Collects daily metrics from Garmin
- Steps
- Calories
- Resting heart rate

### Sleep data
- Total sleep duration
- Sleep score
- Deep / Light / REM / Awake breakdown

### Recovery signals
- HRV
- Training readiness
- Body battery
- Stress

### Training load
- Weekly training load
- Acute / chronic load
- Training status
- ACWR (acute:chronic ratio)

### Additional physiology
- Respiration rate
- SpO₂ (blood oxygen)

### Body composition (Garmin Index S2 scale)
- Weight (kg)
- Body fat %
- BMI
- Body water %
- Muscle mass (kg)
- Bone mass (kg)

---

## How authentication works

This project uses the `garminconnect` Python library.

Authentication is handled via stored tokens.

### First-time setup

Run once to create tokens:

```bash
python login_once.py
```

This will:
- prompt for Garmin credentials
- create a token file in:

```text
garmin/tokens/garmin_tokens.json
```

This file is **not committed to Git** (see `.gitignore`).

---

## Configuration

Create a `.env` file in the `garmin/` directory.

### Example (`.env.example`)

```env
GARMIN_EMAIL=your_email_here
GARMIN_PASSWORD=your_password_here

DB_HOST=your_db_host
DB_PORT=5432
DB_NAME=postgres
DB_USER=postgres
DB_PASSWORD=your_password_here

GARMINTOKENS=/app/tokens
```

Then copy:

```bash
cp .env.example .env
```

---

## Database requirements

Data is written to:

```text
public.garmin_daily
```

This table includes fields like:

- `date`
- `steps`
- `hrv`
- `training_readiness`
- `training_load`
- `sleep_score`
- `body_fat_pct`
- `weight_kg`
- `muscle_mass`
- and related recovery / body-composition fields

The sync script:
- automatically adapts to existing columns
- only writes fields that exist
- uses `ON CONFLICT (date) DO UPDATE`

---

## Running locally

Inside the `garmin/` directory:

```bash
python garmin_sync.py
```

---

## Docker usage

### Build image

```bash
docker build -t garmin-sync:latest .
```

### Run container

```bash
docker run --env-file .env garmin-sync:latest
```

---

## Token storage

Tokens are stored in:

```text
garmin/tokens/
```

Files include:

- `garmin_tokens.json`
- `garmin_tokens.json.bak`

These are:
- required for login
- excluded from Git

---

## Garmin data caveats

### Body composition metrics

- Weight and body fat are the most reliable.
- Muscle mass and body water are useful mainly as **trends**, not exact lab-grade measurements.
- Fields like `physique_rating`, `visceral_fat`, and `metabolic_age` may not be provided by Garmin or may legitimately return `null` depending on device support and what Garmin exposes.

### Timing behavior

Garmin only returns scale/body data if:

- you actually weighed yourself that day

If no weigh-in occurs:
- body composition fields return `null`

---

## Troubleshooting

### No data appearing

Check:
- Garmin login tokens exist
- `.env` values are correct
- DB connection works

### Missing body composition data

- Confirm weigh-in was recorded in Garmin Connect
- Re-run sync after the device / app finishes syncing
- Check logs for the `daily_weigh_ins` payload

### Sync succeeds but fields are NULL

Common causes:
- No weigh-in that day
- Garmin API does not expose the metric for your setup
- The connected device does not support that measurement

---

## Project context

This service feeds data into:

- analytics views (training load, fatigue, recovery)
- MCP tools used by Claude
- a combined fitness coaching system

---

## Typical downstream use cases

This data is used for:

- correlating training load vs recovery
- identifying under-recovery or overtraining
- analyzing sleep versus performance relationships
- tracking weight and body composition trends

---

## Suggested companion files

Recommended files to keep in the `garmin/` folder:

```text
garmin/
├── .env.example
├── Dockerfile
├── garmin_sync.py
├── login_once.py
├── README.md
├── requirements.txt
└── tokens/
```

---

## Git / security notes

Do **not** commit:

- `.env`
- `tokens/`
- `garmin_tokens.json`
- `garmin_tokens.json.bak`

A good `.gitignore` should exclude those.

---

## Next steps inside the larger project

Typical follow-on work from this service includes:

- exposing the Garmin data to Claude via MCP tools
- combining Garmin recovery with Strava ride load and Hevy lifting volume
- building higher-level views for fatigue, underfueling, and readiness analysis
