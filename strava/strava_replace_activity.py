#!/usr/bin/env python3
"""
Auto-sync Garmin activities to Strava via FIT upload.
Runs hourly — queries Garmin for recent activities, checks DB for what's
already on Strava, and uploads FIT files for anything missing.

Can also be run manually with explicit IDs:
  python strava_replace_activity.py --garmin-id 23119214174

When Garmin auto-upload to Strava is ON, a fresh upload of the same
activity will be rejected by Strava as a duplicate - the script just
skips it, leaving the stale pre-enrichment version in place. Use
--hevy-id + --garmin-id together to update-in-place instead (see
run_update_description below) - Strava won't let third-party apps delete
activities they didn't create, but will let them PUT new metadata onto
any activity the authenticated athlete owns.
"""

import os
import sys
import zipfile
import io
import time
import json
import argparse
import datetime
import requests
import psycopg2
from dotenv import load_dotenv
from garminconnect import Garmin

# ── Env ──────────────────────────────────────────────────────────────────────
load_dotenv()

GARMIN_TOKEN_DIR     = os.getenv("GARMINTOKENS", "/root/.garminconnect")
STRAVA_CLIENT_ID     = os.environ["STRAVA_CLIENT_ID"]
STRAVA_CLIENT_SECRET = os.environ["STRAVA_CLIENT_SECRET"]
STRAVA_TOKENS_FILE   = os.environ["STRAVA_TOKENS_FILE"]
DB_HOST              = os.environ["DB_HOST"]
DB_PORT              = os.environ.get("DB_PORT", "5432")
DB_NAME              = os.environ.get("DB_NAME", "postgres")
DB_USER              = os.environ.get("DB_USER", "postgres")
DB_PASSWORD          = os.environ["DB_PASSWORD"]
LOOKBACK_DAYS        = int(os.environ.get("STRAVA_LOOKBACK_DAYS", "3"))

# Minimum FIT size in bytes — unenhanced FITs from Garmin are very small.
# If below this threshold, hevy2garmin probably hasn't run yet. Retry.
MIN_FIT_BYTES        = 5000
FIT_RETRY_ATTEMPTS   = 3
FIT_RETRY_DELAY      = 30  # seconds between retries

# Garmin activity type -> Strava sport_type
ACTIVITY_TYPE_MAP = {
    "strength_training":   "WeightTraining",
    "road_biking":         "Ride",
    "indoor_cycling":      "VirtualRide",
    "cycling":             "Ride",
    "walking":             "Walk",
    "running":             "Run",
    "trail_running":       "TrailRun",
    "hiking":              "Hike",
    "swimming":            "Swim",
    "open_water_swimming": "Swim",
}

TRAINER_TYPES = {"WeightTraining", "VirtualRide"}


# ── Garmin client ───────────────────────────────────────────────────────────
def get_garmin_client():
    client = Garmin()
    client.login(tokenstore=GARMIN_TOKEN_DIR)
    return client


# ── Strava token helpers ──────────────────────────────────────────────────────
def load_tokens():
    with open(STRAVA_TOKENS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_tokens(token_data):
    tmp = STRAVA_TOKENS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(token_data, f, indent=2)
    os.replace(tmp, STRAVA_TOKENS_FILE)


def get_access_token():
    tokens = load_tokens()
    if time.time() < tokens.get("expires_at", 0) - 60:
        return tokens["access_token"]
    print("  Strava token expired — refreshing...")
    resp = requests.post("https://www.strava.com/oauth/token", data={
        "client_id":     STRAVA_CLIENT_ID,
        "client_secret": STRAVA_CLIENT_SECRET,
        "grant_type":    "refresh_token",
        "refresh_token": tokens["refresh_token"],
    }, timeout=30)
    resp.raise_for_status()
    new = resp.json()
    save_tokens({
        "access_token":  new["access_token"],
        "refresh_token": new["refresh_token"],
        "expires_at":    new["expires_at"],
    })
    print("  Token refreshed and saved")
    return new["access_token"]


# ── DB helpers ───────────────────────────────────────────────────────────────
def get_db_conn():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD
    )


def get_existing_strava_dates(conn, since_date):
    """Return set of (date_str, sport_type) tuples already in strava_activities."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DATE(activity_date AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York'),
                   sport_type
            FROM strava_activities
            WHERE activity_date >= %s
            """,
            (since_date,)
        )
        return {(str(row[0]), row[1]) for row in cur.fetchall()}


def find_strava_activity_id(conn, activity_date, sport_type):
    """
    Look up the Strava activity ID already on file for a given date/sport_type.
    This is the activity Garmin's own auto-upload already pushed to Strava -
    we can't delete it (Strava blocks delete on activities we didn't create),
    but we can PUT a corrected description onto it.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT strava_activity_id
            FROM strava_activities
            WHERE DATE(activity_date AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York') = %s
              AND sport_type = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (activity_date, sport_type)
        )
        row = cur.fetchone()
        return row[0] if row else None


def get_hevy_workout_date(conn, hevy_id):
    """
    Return the local date (YYYY-MM-DD) this Hevy workout started, so
    run_update_description can confirm --hevy-id and --garmin-id actually
    refer to the same session before writing anything. Without this check,
    two mismatched IDs get processed independently with no cross-validation -
    each resolves fine on its own, and the result is one workout's data
    landing on a different day's Strava activity.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DATE(start_time AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York')
            FROM hevy_workouts
            WHERE workout_id = %s
            """,
            (hevy_id,)
        )
        row = cur.fetchone()
        return str(row[0]) if row else None


def build_hevy_description(conn, hevy_id):
    """
    Build a human-readable exercise summary from Hevy's own set data
    (hevy-sync's tables, not hevy2garmin's - h2g doesn't persist the
    description it generates for Garmin anywhere queryable).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.exercise_index, e.title,
                   s.set_index, s.set_type, s.weight_kg, s.reps
            FROM hevy_workout_exercises e
            JOIN hevy_workout_sets s
              ON s.workout_id = e.workout_id AND s.exercise_index = e.exercise_index
            WHERE e.workout_id = %s
            ORDER BY e.exercise_index, s.set_index
            """,
            (hevy_id,)
        )
        rows = cur.fetchall()

    if not rows:
        return None

    exercises = {}
    for exercise_index, title, set_index, set_type, weight_kg, reps in rows:
        exercises.setdefault((exercise_index, title), []).append((set_type, weight_kg, reps))

    lines = []
    for (idx, title), sets in sorted(exercises.items()):
        set_strs = []
        for set_type, weight_kg, reps in sets:
            weight_lb = round(float(weight_kg) * 2.20462) if weight_kg else None
            piece = f"{reps}x{weight_lb}lb" if weight_lb else f"{reps} reps"
            if set_type and set_type.lower() == "warmup":
                piece += " (warmup)"
            set_strs.append(piece)
        lines.append(f"{title}: " + ", ".join(set_strs))

    return "\n".join(lines) + "\n\n— synced by hevy2garmin pipeline"


# ── Download FIT with size check + retry ──────────────────────────────────────
def download_fit(client, garmin_activity_id, min_bytes=MIN_FIT_BYTES):
    """
    Download FIT from Garmin. If the file is suspiciously small (hevy2garmin
    hasn't enhanced it yet), wait and retry up to FIT_RETRY_ATTEMPTS times.
    """
    for attempt in range(1, FIT_RETRY_ATTEMPTS + 1):
        zip_bytes = client.download_activity(
            garmin_activity_id,
            dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL
        )
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            fit_names = [n for n in z.namelist() if n.endswith(".fit")]
            if not fit_names:
                raise RuntimeError(f"No .fit in zip. Contents: {z.namelist()}")
            fit_filename = fit_names[0]
            fit_data = z.read(fit_filename)

        print(f"      FIT size: {len(fit_data)} bytes (attempt {attempt}/{FIT_RETRY_ATTEMPTS})")

        if len(fit_data) >= min_bytes:
            return fit_filename, fit_data

        if attempt < FIT_RETRY_ATTEMPTS:
            print(f"      FIT too small ({len(fit_data)}B < {min_bytes}B) — hevy2garmin may not have run yet. Waiting {FIT_RETRY_DELAY}s...")
            time.sleep(FIT_RETRY_DELAY)
        else:
            print(f"      WARNING: FIT still small after {FIT_RETRY_ATTEMPTS} attempts. Uploading anyway.")

    return fit_filename, fit_data


# ── Upload FIT to Strava ───────────────────────────────────────────────────────
def upload_fit(fit_filename, fit_data, activity_name, sport_type, access_token):
    is_trainer = "1" if sport_type in TRAINER_TYPES else "0"
    resp = requests.post(
        "https://www.strava.com/api/v3/uploads",
        headers={"Authorization": f"Bearer {access_token}"},
        data={
            "data_type":  "fit",
            "name":       activity_name,
            "sport_type": sport_type,
            "trainer":    is_trainer,
        },
        files={"file": (fit_filename, fit_data, "application/octet-stream")},
        timeout=30,
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Upload failed [{resp.status_code}]: {resp.text}")
    return resp.json().get("id")


def poll_upload(upload_id, access_token):
    for attempt in range(12):
        time.sleep(5)
        poll = requests.get(
            f"https://www.strava.com/api/v3/uploads/{upload_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        )
        data            = poll.json()
        error           = data.get("error", "")
        new_activity_id = data.get("activity_id")

        # Treat duplicate as success — activity already exists on Strava
        if error and "duplicate" in error.lower():
            print(f"      Duplicate detected — activity already on Strava, skipping.")
            return None

        if error:
            raise RuntimeError(f"Upload error: {error}")
        if new_activity_id:
            return new_activity_id

    raise RuntimeError("Timed out waiting for Strava upload")


# ── Update an existing Strava activity's description (update-in-place) ────────
def update_strava_description(strava_activity_id, description, access_token):
    """
    PUT a new description onto an existing Strava activity. Works on any
    activity the authenticated athlete owns, regardless of which app created
    it (confirmed live against activity 19072431927 - DELETE returns 401,
    PUT returns 200). Does NOT replace the underlying recording/streams -
    only Strava's own first-party upload can do that.
    """
    resp = requests.put(
        f"https://www.strava.com/api/v3/activities/{strava_activity_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        data={"description": description},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Description update failed [{resp.status_code}]: {resp.text}")
    return resp.json()


# ── Auto mode: find and upload missing activities ─────────────────────────────
def run_auto(lookback_days):
    today     = datetime.date.today()
    since     = today - datetime.timedelta(days=lookback_days)
    since_str = since.isoformat()

    print(f"Auto mode: checking last {lookback_days} days ({since_str} to {today})")

    print("Connecting to Garmin...")
    garmin = get_garmin_client()
    print(f"  Authenticated as: {garmin.get_full_name()}")

    print("Fetching Garmin activities...")
    garmin_activities = garmin.get_activities_by_date(since_str, today.isoformat())
    print(f"  Found {len(garmin_activities)} Garmin activities")

    print("Checking DB for existing Strava activities...")
    conn = get_db_conn()
    existing = get_existing_strava_dates(conn, since_str)
    conn.close()
    print(f"  Found {len(existing)} existing Strava entries in window")

    access_token = get_access_token()

    uploaded = 0
    skipped  = 0
    errors   = 0

    for a in garmin_activities:
        activity_id   = a.get("activityId")
        activity_name = a.get("activityName", "Activity")
        type_key      = a.get("activityType", {}).get("typeKey", "")
        start_local   = a.get("startTimeLocal", "")
        activity_date = start_local[:10] if start_local else None

        sport_type = ACTIVITY_TYPE_MAP.get(type_key)

        if not sport_type:
            print(f"  SKIP {activity_name} ({type_key}) — unmapped type")
            skipped += 1
            continue

        if (activity_date, sport_type) in existing:
            print(f"  SKIP {activity_name} on {activity_date} — already on Strava")
            skipped += 1
            continue

        print(f"  UPLOAD {activity_name} | {activity_date} | {sport_type} | Garmin ID {activity_id}")
        try:
            fit_filename, fit_data = download_fit(garmin, activity_id)
            upload_id              = upload_fit(fit_filename, fit_data, activity_name, sport_type, access_token)
            if upload_id is None:
                print(f"    ⚠️  Duplicate — skipped")
                skipped += 1
            else:
                new_id = poll_upload(upload_id, access_token)
                if new_id:
                    print(f"    ✅ https://www.strava.com/activities/{new_id}")
                    uploaded += 1
                else:
                    skipped += 1
            time.sleep(2)
        except Exception as e:
            print(f"    ❌ Failed: {e}")
            errors += 1

    print(f"\nDone. uploaded={uploaded} skipped={skipped} errors={errors}")
    if errors:
        sys.exit(1)


# ── Manual mode: explicit Garmin ID ───────────────────────────────────────────
def run_manual(garmin_id, sport_type_override, name_override):
    print(f"Manual mode: Garmin activity {garmin_id}")

    print("Connecting to Garmin...")
    garmin = get_garmin_client()
    print(f"  Authenticated as: {garmin.get_full_name()}")

    activities    = garmin.get_activity(garmin_id)
    activity_name = name_override or activities.get("activityName", "Activity")
    type_key      = activities.get("activityTypeDTO", {}).get("typeKey", "")
    sport_type    = sport_type_override or ACTIVITY_TYPE_MAP.get(type_key)

    if not sport_type:
        print(f"  ERROR: Unknown activity type '{type_key}'. Use --sport-type to override.")
        sys.exit(1)

    print(f"  Activity: {activity_name} | type: {type_key} -> {sport_type}")

    access_token = get_access_token()

    print(f"Downloading FIT...")
    fit_filename, fit_data = download_fit(garmin, garmin_id)
    print(f"  {fit_filename} ({len(fit_data)} bytes)")

    print(f"Uploading to Strava...")
    upload_id = upload_fit(fit_filename, fit_data, activity_name, sport_type, access_token)
    if upload_id is None:
        print(f"\n⚠️  Duplicate — activity already on Strava.")
        return
    new_id = poll_upload(upload_id, access_token)
    if new_id:
        print(f"\n✅  https://www.strava.com/activities/{new_id}")


# ── Update-in-place mode: hevy id + garmin id ──────────────────────────────────
def run_update_description(hevy_id, garmin_id):
    """
    Used after hevy2garmin has merged set data into a Garmin watch-recorded
    activity, while Garmin auto-upload is still on. Strava already has a
    copy (pushed by Garmin before the merge ran), so a fresh upload would
    just bounce as a duplicate. Instead: find that existing Strava activity
    and PUT the real exercise/set data onto its description.
    """
    print(f"Update-description mode: hevy_id={hevy_id} garmin_id={garmin_id}")

    print("Connecting to Garmin...")
    garmin = get_garmin_client()

    activity     = garmin.get_activity(garmin_id)
    type_key     = activity.get("activityTypeDTO", {}).get("typeKey", "")
    sport_type   = ACTIVITY_TYPE_MAP.get(type_key)
    start_local  = (activity.get("summaryDTO", {}) or {}).get("startTimeLocal") or activity.get("startTimeLocal")
    activity_date = (start_local or "")[:10]

    if not sport_type or not activity_date:
        print(f"  ERROR: could not resolve sport_type/date for Garmin activity {garmin_id}")
        sys.exit(1)

    print(f"  Garmin activity: {activity_date} | {sport_type}")

    conn = get_db_conn()

    hevy_date = get_hevy_workout_date(conn, hevy_id)
    if not hevy_date:
        conn.close()
        print(f"  ERROR: Hevy workout {hevy_id} not found — can't verify it matches this Garmin activity.")
        sys.exit(1)

    if hevy_date != activity_date:
        conn.close()
        print(f"  ERROR: date mismatch — Hevy workout {hevy_id} is from {hevy_date}, "
              f"but Garmin activity {garmin_id} is from {activity_date}.")
        print(f"  Refusing to write mismatched data. Double-check --hevy-id and --garmin-id "
              f"actually refer to the same session.")
        sys.exit(1)

    strava_id = find_strava_activity_id(conn, activity_date, sport_type)

    if not strava_id:
        conn.close()
        print(f"  No matching Strava activity found for {activity_date} {sport_type} — nothing to update.")
        print(f"  (If Garmin auto-upload hasn't pushed it yet, this is expected — retry later.)")
        sys.exit(2)

    print(f"  Matched Strava activity: {strava_id}")

    description = build_hevy_description(conn, hevy_id)
    conn.close()

    if not description:
        print(f"  WARNING: no Hevy set data found for hevy_id={hevy_id} — skipping description update.")
        sys.exit(2)

    access_token = get_access_token()
    update_strava_description(strava_id, description, access_token)
    print(f"  ✅ Updated description on https://www.strava.com/activities/{strava_id}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Sync Garmin activities to Strava via FIT upload or description update")
    parser.add_argument("--garmin-id",  type=int, help="Manual/update mode: specific Garmin activity ID")
    parser.add_argument("--hevy-id",    type=str, help="Update-description mode: Hevy workout ID (use with --garmin-id)")
    parser.add_argument("--sport-type", type=str, help="Manual mode: override Strava sport type")
    parser.add_argument("--name",       type=str, help="Manual mode: override activity name")
    parser.add_argument("--lookback",   type=int, default=LOOKBACK_DAYS, help="Auto mode: days to look back (default: 3)")
    args = parser.parse_args()

    if args.hevy_id and args.garmin_id:
        run_update_description(args.hevy_id, args.garmin_id)
    elif args.garmin_id:
        run_manual(args.garmin_id, args.sport_type, args.name)
    else:
        run_auto(args.lookback)


if __name__ == "__main__":
    main()
