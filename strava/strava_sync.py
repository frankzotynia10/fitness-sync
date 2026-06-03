import os
import sys
import json
import time
import datetime
import psycopg2
import requests
from dotenv import load_dotenv

from services.strava_streams import normalize_strava_streams
from services.fit_encoder import FitEncoder
from services.power_sync import (
    ensure_power_tables,
    upsert_activity_stream_rows,
    upsert_best_efforts,
)

load_dotenv()

STRAVA_CLIENT_ID = os.environ["STRAVA_CLIENT_ID"]
STRAVA_CLIENT_SECRET = os.environ["STRAVA_CLIENT_SECRET"]
STRAVA_TOKENS_FILE = os.environ["STRAVA_TOKENS_FILE"]

DB_HOST = os.environ["DB_HOST"]
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "postgres")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ["DB_PASSWORD"]

STREAM_KEYS = ["time", "distance", "watts", "heartrate", "cadence", "velocity_smooth"]


def load_tokens():
    with open(STRAVA_TOKENS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_tokens(token_data):
    tmp_file = STRAVA_TOKENS_FILE + ".tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(token_data, f, indent=2)
    os.replace(tmp_file, STRAVA_TOKENS_FILE)


def refresh_access_token(refresh_token):
    resp = requests.post(
        "https://www.strava.com/oauth/token",
        data={
            "client_id": STRAVA_CLIENT_ID,
            "client_secret": STRAVA_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_recent_activities(access_token, page=1, per_page=30):
    resp = requests.get(
        "https://www.strava.com/api/v3/athlete/activities",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"page": page, "per_page": per_page},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_activity_streams(access_token, activity_id, keys=None):
    if keys is None:
        keys = STREAM_KEYS
    resp = requests.get(
        f"https://www.strava.com/api/v3/activities/{activity_id}/streams",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"keys": ",".join(keys), "key_by_type": "true"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def delete_strava_activity(access_token, activity_id):
    """Delete a Strava activity."""
    resp = requests.delete(
        f"https://www.strava.com/api/v3/activities/{activity_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    resp.raise_for_status()


def upload_fit_file(access_token, fit_bytes, activity_name):
    """Upload a FIT file to Strava. Returns upload ID."""
    resp = requests.post(
        "https://www.strava.com/api/v3/uploads",
        headers={"Authorization": f"Bearer {access_token}"},
        data={
            "data_type": "fit",
            "name": activity_name,
            "activity_type": "weight_training",
        },
        files={"file": (f"{activity_name}.fit", fit_bytes, "application/octet-stream")},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def poll_upload_status(access_token, upload_id, max_attempts=15, interval=2):
    """Poll Strava upload status until complete or failed."""
    for attempt in range(max_attempts):
        resp = requests.get(
            f"https://www.strava.com/api/v3/uploads/{upload_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status", "")
        error = data.get("error", "")
        activity_id = data.get("activity_id")

        print(f"  Upload status ({attempt+1}): {status}")

        if error:
            raise RuntimeError(f"Upload failed: {error}")
        if activity_id:
            return activity_id
        if "Your activity is ready" in status or "deleted" in status:
            return activity_id

        time.sleep(interval)

    raise RuntimeError(f"Upload timed out after {max_attempts} attempts")


def get_workout_data_for_fit(workout_id, conn):
    """Fetch Hevy workout data formatted for FIT encoding."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT title, start_time, end_time
            FROM hevy_workouts WHERE workout_id = %s
        """, (workout_id,))
        row = cur.fetchone()
        if not row:
            return None
        title, start_time, end_time = row

        cur.execute("""
            SELECT
                hwe.title AS exercise_name,
                hwe.exercise_index,
                hws.set_index,
                hws.weight_kg,
                hws.reps,
                hws.set_type
            FROM hevy_workout_exercises hwe
            JOIN hevy_workout_sets hws
              ON hws.workout_id = hwe.workout_id
             AND hws.exercise_index = hwe.exercise_index
            WHERE hwe.workout_id = %s
              AND hws.set_type IN ('normal', 'warmup')
            ORDER BY hwe.exercise_index, hws.set_index
        """, (workout_id,))
        rows = cur.fetchall()

    exercises = {}
    for ex_name, ex_idx, set_idx, weight_kg, reps, set_type in rows:
        if ex_idx not in exercises:
            exercises[ex_idx] = {"name": ex_name, "sets": []}
        exercises[ex_idx]["sets"].append({
            "weight_kg": weight_kg,
            "reps": reps,
            "set_type": set_type,
        })

    return {
        "title": title,
        "start_time": start_time,
        "end_time": end_time,
        "exercises": [exercises[i] for i in sorted(exercises.keys())],
    }


def upload_hevy_workouts_as_fit(access_token, conn, lookback_days=7):
    """
    For each recent Hevy workout:
    1. Generate a FIT file with structured set data
    2. Upload to Strava
    3. Delete the matching Garmin WeightTraining duplicate
    """
    print(f"\nUploading Hevy workouts as FIT files (last {lookback_days} days)...")

    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                hw.workout_id,
                hw.title,
                hw.start_time,
                sa.strava_activity_id AS garmin_activity_id,
                sa.name AS garmin_name
            FROM hevy_workouts hw
            LEFT JOIN strava_activities sa
              ON sa.sport_type = 'WeightTraining'
             AND ABS(EXTRACT(EPOCH FROM (sa.activity_date - hw.start_time))) < 3600
             AND sa.description_updated_at IS NULL
            WHERE hw.start_time >= NOW() - (%s * INTERVAL '1 day')
            ORDER BY hw.start_time DESC
        """, (lookback_days,))
        matches = cur.fetchall()

    if not matches:
        print("  No workouts to process.")
        return

    encoder = FitEncoder()

    for workout_id, title, start_time, garmin_activity_id, garmin_name in matches:
        print(f"\n  Processing: '{title}' ({workout_id})")

        workout_data = get_workout_data_for_fit(workout_id, conn)
        if not workout_data:
            print(f"  ⚠️ No data for workout {workout_id}, skipping.")
            continue

        # Generate FIT file
        try:
            fit_bytes = encoder.encode(workout_data)
            print(f"  Generated FIT file: {len(fit_bytes)} bytes")
        except Exception as e:
            print(f"  ⚠️ FIT encoding failed: {e}")
            continue

        # Upload to Strava
        try:
            upload_resp = upload_fit_file(access_token, fit_bytes, title)
            upload_id = upload_resp.get("id")
            print(f"  Upload initiated: {upload_id}")

            new_activity_id = poll_upload_status(access_token, upload_id)
            print(f"  ✅ New Strava activity created: {new_activity_id}")
        except Exception as e:
            print(f"  ⚠️ Upload failed: {e}")
            continue

        # Delete Garmin duplicate if found
        if garmin_activity_id:
            try:
                delete_strava_activity(access_token, garmin_activity_id)
                print(f"  🗑️ Deleted Garmin duplicate: {garmin_activity_id} ('{garmin_name}')")
            except Exception as e:
                print(f"  ⚠️ Failed to delete Garmin duplicate {garmin_activity_id}: {e}")

        # Mark as processed in DB
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE strava_activities
                    SET description_updated_at = NOW()
                    WHERE strava_activity_id = %s
                """, (garmin_activity_id,))

    print("\nFIT upload complete.")


def upsert_activities(conn, activities):
    with conn:
        with conn.cursor() as cur:
            for a in activities:
                cur.execute("""
                    INSERT INTO strava_activities (
                        strava_activity_id, activity_date, name, sport_type,
                        distance_m, moving_time_s, elapsed_time_s, total_elevation_gain_m,
                        average_speed, max_speed, average_heartrate, max_heartrate,
                        average_watts, weighted_average_watts, max_watts, kilojoules,
                        trainer, commute, manual, private, raw_json, updated_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW()
                    )
                    ON CONFLICT (strava_activity_id) DO UPDATE SET
                        activity_date = EXCLUDED.activity_date,
                        name = EXCLUDED.name,
                        sport_type = EXCLUDED.sport_type,
                        distance_m = EXCLUDED.distance_m,
                        moving_time_s = EXCLUDED.moving_time_s,
                        elapsed_time_s = EXCLUDED.elapsed_time_s,
                        total_elevation_gain_m = EXCLUDED.total_elevation_gain_m,
                        average_speed = EXCLUDED.average_speed,
                        max_speed = EXCLUDED.max_speed,
                        average_heartrate = EXCLUDED.average_heartrate,
                        max_heartrate = EXCLUDED.max_heartrate,
                        average_watts = EXCLUDED.average_watts,
                        weighted_average_watts = EXCLUDED.weighted_average_watts,
                        max_watts = EXCLUDED.max_watts,
                        kilojoules = EXCLUDED.kilojoules,
                        trainer = EXCLUDED.trainer,
                        commute = EXCLUDED.commute,
                        manual = EXCLUDED.manual,
                        private = EXCLUDED.private,
                        raw_json = EXCLUDED.raw_json,
                        updated_at = NOW();
                """, (
                    a.get("id"), a.get("start_date"), a.get("name"), a.get("sport_type"),
                    a.get("distance"), a.get("moving_time"), a.get("elapsed_time"),
                    a.get("total_elevation_gain"), a.get("average_speed"), a.get("max_speed"),
                    a.get("average_heartrate"), a.get("max_heartrate"), a.get("average_watts"),
                    a.get("weighted_average_watts"), a.get("max_watts"), a.get("kilojoules"),
                    a.get("trainer"), a.get("commute"), a.get("manual"), a.get("private"),
                    json.dumps(a),
                ))


def upsert_activity_streams(conn, activity_id, streams):
    if not streams or not isinstance(streams, dict):
        return
    with conn:
        with conn.cursor() as cur:
            cur.execute("delete from strava_activity_streams where strava_activity_id = %s", (activity_id,))
            inserted_count = 0
            for stream_type, stream_obj in streams.items():
                if not isinstance(stream_obj, dict):
                    continue
                data = stream_obj.get("data", [])
                if not isinstance(data, list):
                    continue
                print(f"  -> stream_type={stream_type}, points={len(data)}")
                for idx, value in enumerate(data):
                    value_numeric = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
                    value_text = value if isinstance(value, str) else None
                    value_json = json.dumps(value) if value_numeric is None and value_text is None else None
                    cur.execute("""
                        insert into strava_activity_streams
                            (strava_activity_id, stream_type, idx, value_numeric, value_text, value_json, updated_at)
                        values (%s, %s, %s, %s, %s, %s::jsonb, now())
                        on conflict (strava_activity_id, stream_type, idx)
                        do update set
                            value_numeric = excluded.value_numeric,
                            value_text = excluded.value_text,
                            value_json = excluded.value_json,
                            updated_at = now();
                    """, (activity_id, stream_type, idx, value_numeric, value_text, value_json))
                    inserted_count += 1
            print(f"  -> {inserted_count} stream rows upserted for {activity_id}")


def parse_start_time(start_date_value):
    if not start_date_value:
        return None
    if isinstance(start_date_value, datetime.datetime):
        return start_date_value
    if isinstance(start_date_value, str):
        try:
            return datetime.datetime.fromisoformat(start_date_value.replace("Z", "+00:00"))
        except Exception:
            return None
    return None


def main():
    try:
        print("Loading Strava tokens from file...")
        tokens = load_tokens()

        print("Refreshing Strava token...")
        new_tokens = refresh_access_token(tokens["refresh_token"])
        save_tokens({
            "access_token": new_tokens["access_token"],
            "refresh_token": new_tokens["refresh_token"],
            "expires_at": new_tokens["expires_at"],
        })
        print("✅ Token refreshed and saved")
        access_token = new_tokens["access_token"]

        print("Fetching recent Strava activities...")
        activities = fetch_recent_activities(access_token)
        print(f"Fetched {len(activities)} activities")

        for a in activities:
            print(f"  id={a.get('id')} sport_type={a.get('sport_type')} name={a.get('name')}")

        print("Connecting to Postgres...")
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD,
        )

        ensure_power_tables(conn)
        upsert_activities(conn, activities)

        # Upload Hevy workouts as structured FIT files, delete Garmin duplicates
        upload_hevy_workouts_as_fit(access_token, conn, lookback_days=7)

        # Fetch streams for rides
        ride_candidates = [
            a for a in activities
            if a.get("sport_type") in ("Ride", "VirtualRide", "EBikeRide")
        ]
        print(f"\nFetching streams for {len(ride_candidates)} ride activities...")

        for a in ride_candidates:
            activity_id = a.get("id")
            if not activity_id:
                continue
            try:
                streams = fetch_activity_streams(access_token, activity_id)
                upsert_activity_streams(conn, activity_id, streams)

                start_time = parse_start_time(a.get("start_date"))
                normalized_rows = normalize_strava_streams(streams, activity_start_time=start_time)

                if normalized_rows:
                    row_count = upsert_activity_stream_rows(conn=conn, activity_id=activity_id, rows=normalized_rows, source="strava")
                    power_values = [row.get("power_w") for row in normalized_rows]
                    upsert_best_efforts(conn=conn, activity_id=activity_id, power_values=power_values, source="strava", windows=[5, 60, 300, 1200])
                    print(f"✅ Power sync complete for {activity_id} (rows={row_count})")

            except Exception as e:
                print(f"⚠️ Stream sync failed for activity {activity_id}: {e}")

        conn.close()
        print("\nStrava sync complete.")

    except Exception as e:
        print(f"Strava sync failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
    sys.exit(0)
