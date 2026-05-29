Garmin Sync Notes (Unraid / Docker / Supabase)
===========================================

Purpose
-------
This note is a quick future reference for:
1. Generating / refreshing the Garmin token
2. Updating the sync script
3. Rebuilding the Docker image
4. Running the sync again
5. Verifying data in Supabase

Folder Layout
-------------
Expected folder:

/mnt/user/appdata/fitness-sync/garmin/

Contents:
- Dockerfile
- requirements.txt
- login_once.py
- garmin_sync.py
- .env
- tokens/

Important files:
- tokens/garmin_tokens.json  -> saved Garmin auth token
- .env                      -> DB connection settings for sync job
- garmin_sync.py            -> main Garmin -> Supabase sync script

============================================================
1. FIRST-TIME LOGIN / REGENERATE TOKEN
============================================================

Use this when:
- setting up from scratch
- Garmin token expired / stopped working
- Garmin password changed
- Garmin MFA/session got invalidated

From the folder:

cd /mnt/user/appdata/fitness-sync/garmin

Build image (or rebuild after edits):

docker build --no-cache -t garmin-sync:latest .

Run interactive login:

docker run --rm -it \
  -e GARMIN_EMAIL="YOUR_GARMIN_EMAIL" \
  -e GARMIN_PASSWORD="YOUR_GARMIN_PASSWORD" \
  -e GARMINTOKENS="/root/.garminconnect" \
  -v /mnt/user/appdata/fitness-sync/garmin/tokens:/root/.garminconnect \
  garmin-sync:latest python login_once.py

Notes:
- If Garmin asks for MFA, enter the code when prompted.
- A successful login should create:

  /mnt/user/appdata/fitness-sync/garmin/tokens/garmin_tokens.json

Verify token exists:

ls -la /mnt/user/appdata/fitness-sync/garmin/tokens

Recommended:
- If credentials were ever exposed in shell history/screenshots, change the Garmin password.
- Optionally make a backup copy of the token file.

============================================================
2. NORMAL SYNC RUN
============================================================

Normal sync uses the saved token file and does NOT need username/password.

The stack in Docker Compose Manager should point to:
- image: garmin-sync:latest
- env_file: /mnt/user/appdata/fitness-sync/garmin/.env
- volume mount: /mnt/user/appdata/fitness-sync/garmin/tokens:/root/.garminconnect

If running manually from CLI for testing:

docker run --rm -it \
  --env-file /mnt/user/appdata/fitness-sync/garmin/.env \
  -v /mnt/user/appdata/fitness-sync/garmin/tokens:/root/.garminconnect \
  garmin-sync:latest

============================================================
3. IF YOU UPDATE garmin_sync.py / Dockerfile / requirements.txt
============================================================

Any time you edit code or dependencies:

cd /mnt/user/appdata/fitness-sync/garmin

docker build --no-cache -t garmin-sync:latest .

Then rerun the container from Docker Compose Manager (or manually from CLI).

If using Docker Compose Manager:
- stack uses image: garmin-sync:latest
- NO build: line (buildx issue in Unraid Compose Manager)
- after rebuild, rerun / redeploy the stack

============================================================
4. .env FILE
============================================================

Typical .env contents:

DB_HOST=YOUR_UNRAID_OR_SUPABASE_HOST_IP
DB_PORT=5432
DB_NAME=postgres
DB_USER=postgres
DB_PASSWORD=YOUR_DB_PASSWORD
GARMINTOKENS=/root/.garminconnect

Notes:
- No quotes in .env values.
- Garmin email/password are NOT needed in .env for normal sync runs if the token file already exists.

============================================================
5. CURRENT METRICS WRITTEN TO garmin_daily
============================================================

Current working fields:
- steps
- calories
- resting_hr
- sleep_seconds
- sleep_score
- body_battery
- training_readiness
- hrv
- vo2_max
- stress_avg
- stress_max
- heat_acclimation
- endurance_score

If a field starts coming in NULL again:
- add debug payload dumps back into garmin_sync.py
- rebuild image
- rerun
- inspect logs

============================================================
6. USEFUL SQL CHECKS IN SUPABASE
============================================================

Check latest Garmin row:

SELECT
  date,
  steps,
  calories,
  resting_hr,
  sleep_seconds,
  sleep_score,
  body_battery,
  training_readiness,
  hrv,
  vo2_max,
  stress_avg,
  stress_max,
  endurance_score,
  heat_acclimation,
  updated_at
FROM garmin_daily
ORDER BY date DESC;

If needed, add missing columns:

ALTER TABLE garmin_daily
ADD COLUMN IF NOT EXISTS vo2_max REAL,
ADD COLUMN IF NOT EXISTS sleep_score INT,
ADD COLUMN IF NOT EXISTS stress_avg INT,
ADD COLUMN IF NOT EXISTS stress_max INT,
ADD COLUMN IF NOT EXISTS endurance_score REAL,
ADD COLUMN IF NOT EXISTS heat_acclimation REAL;

============================================================
7. TROUBLESHOOTING
============================================================

A) Garmin auth/token issues
---------------------------
Symptom:
- 401 auth errors
- MFA prompt again unexpectedly
- token missing/invalid

Fix:
- rerun login_once.py interactively
- verify garmin_tokens.json exists

B) Build changes not taking effect
----------------------------------
Symptom:
- container still behaves like old code

Fix:
- rebuild with:
  docker build --no-cache -t garmin-sync:latest .

C) Compose Manager fails on buildx
----------------------------------
Symptom:
- compose build requires buildx 0.17.0+

Fix:
- do NOT use build: in Compose Manager
- build manually from CLI
- use image: garmin-sync:latest in stack YAML

D) Need full container logs
---------------------------
Dump logs to file:

docker logs garmin-sync > /mnt/user/appdata/fitness-sync/garmin/garmin-sync-full.log 2>&1

Then inspect:

less /mnt/user/appdata/fitness-sync/garmin/garmin-sync-full.log

============================================================
8. CURRENT OPERATIONAL WORKFLOW
============================================================

Normal workflow for future updates:

1. Edit garmin_sync.py if needed
2. Rebuild image:
   docker build --no-cache -t garmin-sync:latest .
3. Run/redeploy garmin-sync from Unraid Docker Compose Manager
4. Check logs if needed
5. Verify row in Supabase with SQL query

If auth breaks:

1. Run login_once.py interactively
2. Verify token file
3. Run normal sync again

============================================================
9. NICE-TO-HAVE FUTURE IDEAS
============================================================

Possible future additions:
- Garmin activities (if needed as backup / supplement to Strava)
- Historical backfill instead of today-only sync
- Separate weekly metrics table for endurance score
- Strava ingestion
- Cron/scheduler for full automation
- Nutrition and lifting data ingestion

End of note.
