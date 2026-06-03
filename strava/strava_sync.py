import os
import sys
import json
import datetime
import psycopg2
import requests
from dotenv import load_dotenv

from services.strava_streams import normalize_strava_streams
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

# Hevy-synced activity names to exclude — these already have set data from Hevy
HEVY_ROUTINE_NAMES = ["arms1", "arms2", "legs1", "legs2", "legzz", "legs3"]


def load_tokens():
    with open(STRAVA_TOKENS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_tokens(token_data):
    tmp_file = STRAVA_TOKENS_FILE + ".tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(token_data, f, indent=2)
    os.replace(tmp_file, STRAVA_TOKENS_FILE)


def refresh_access_token(refresh_token):
    url = "https://www.strava.com/oauth/token"
    data = {
        "client_id": STRAVA_CLIENT_ID,
        "client_secret": STRAVA_CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    resp = requests.post(url, data=data, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_recent_activities(access_token, page=1, per_page=30):
    url = "https://www.strava.com/api/v3/athlete/activities"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"page": page, "per_page": per_page}
    resp = requests.get(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_activity_streams(access_token, activity_id, keys=None):
    if keys is None:
        keys = STREAM_KEYS
    url = f"https://www.strava.com/api/v3/activities/{activity_id}/streams"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"keys": ",".join(keys), "key_by_type": "true"}
    resp = requests.get(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def update_strava_activity(access_token, activity_id, name=None, description=None):
    """Update a Strava activity name and/or description."""
    url = f"https://www.strava.com/api/v3/activities/{activity_id}"
    headers = {"Authorization": f"Bearer {access_token}"}
    payload = {}
    if name is not None:
        payload["name"] = name
    if description is not None:
        payload["description"] = description
    resp = requests.put(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def build_workout_description(workout_id, conn):
    """Build a text description of a Hevy workout for Strava."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT title,
                   EXTRACT(EPOCH FROM (end_time - start_time)) / 60 AS duration_min
            FROM hevy_workouts
            WHERE workout_id = %s
        """, (workout_id,))
        row = cur.fetchone()
        if not row:
            return None
        title, duration_min = row

        cur.execute("""
            SELECT
                hwe.title AS exercise,
                hwe.exercise_index,
                hws.set_index,
                hws.weight_kg,
                hws.reps,
                hws.rpe,
                hws.set_type,
                hwe.notes
            FROM hevy_workout_exercises hwe
            JOIN hevy_workout_sets hws
              ON hws.workout_id = hwe.workout_id
             AND hws.exercise_index = hwe.exercise_index
            WHERE hwe.workout_id = %s
              AND hws.set_type = 'normal'
            ORDER BY hwe.exercise_index, hws.set_index
        """, (workout_id,))
        sets = cur.fetchall()

    if not sets:
        return None

    exercises = {}
    notes_map = {}
    for exercise, ex_idx, set_idx, weight_kg, reps, rpe, set_type, notes in sets:
        if ex_idx not in exercises:
            exercises[ex_idx] = {"name": exercise, "sets": []}
            if notes:
                notes_map[ex_idx] = notes
        weight_lb = round(float(weight_kg) * 2.20462, 1) if weight_kg else None
        exercises[ex_idx]["sets"].append((weight_lb, reps, rpe))

    lines = [f"{title} | {round(duration_min)} min", ""]

    for ex_idx in sorted(exercises.keys()):
        ex = exercises[ex_idx]
        lines.append(ex["name"])
        set_summary = []
        prev = None
        count = 0
        for weight_lb, reps, rpe in ex["sets"]:
            key = (weight_lb, reps)
            if key == prev:
                count += 1
            else:
                if prev is not None:
                    w, r = prev
                    w_str = f"{w} lb" if w else "BW"
                    set_summary.append(f"{count}×{r} @ {w_str}")
                prev = key
                count = 1
        if prev:
            w, r = prev
            w_str = f"{w} lb" if w else "BW"
            set_summary.append(f"{count}×{r} @ {w_str}")
        lines.append("  " + " | ".join(set_summary))
        if ex_idx in notes_map:
            lines.append(f"  ✎ {notes_map[ex_idx]}")

    return "\n".join(lines)


def update_strength_descriptions(access_token, conn, lookback_days=7):
    """
    For each recent Hevy workout, find the matching Garmin WeightTraining
    activity on Strava (by timestamp, excluding Hevy-named activities)
    and update its name and description with Hevy set data.
    """
    print(f"\nUpdating Strava strength descriptions (last {lookback_days} days)...")

    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                hw.workout_id,
                hw.title,
                hw.start_time,
                hw.end_time,
                sa.strava_activity_id,
                sa.name AS strava_name
            FROM hevy_workouts hw
            JOIN strava_activities sa
              ON sa.sport_type = 'WeightTraining'
             AND ABS(EXTRACT(EPOCH FROM (sa.activity_date - hw.start_time))) < 3600
            WHERE hw.start_time >= NOW() - (%s * INTERVAL '1 day')
              AND LOWER(sa.name) != LOWER(hw.title)
            ORDER BY hw.start_time DESC
        """, (lookback_days,))
        matches = cur.fetchall()

    if not matches:
        print("  No strength activities to update.")
        return

    for workout_id, title, start_time, end_time, strava_id, strava_name in matches:
        print(f"  Matching: Hevy '{title}' -> Strava '{strava_name}' ({strava_id})")

        description = build_workout_description(workout_id, conn)
        if not description:
            print(f"  ⚠️ No set data for workout {workout_id}, skipping.")
            continue

        try:
            update_strava_activity(
                access_token,
                strava_id,
                name=title,
                description=description,
            )
            print(f"  ✅ Updated Strava activity {strava_id} -> '{title}'")
        except Exception as e:
            print(f"  ⚠️ Failed to update {strava_id}: {e}")


def upsert_activities(conn, activities):
    with conn:
        with conn.cursor() as cur:
            for a in activities:
                cur.execute(
                    """
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
                    """,
                    (
                        a.get("id"), a.get("start_date"), a.get("name"), a.get("sport_type"),
                        a.get("distance"), a.get("moving_time"), a.get("elapsed_time"),
                        a.get("total_elevation_gain"), a.get("average_speed"), a.get("max_speed"),
                        a.get("average_heartrate"), a.get("max_heartrate"), a.get("average_watts"),
                        a.get("weighted_average_watts"), a.get("max_watts"), a.get("kilojoules"),
                        a.get("trainer"), a.get("commute"), a.get("manual"), a.get("private"),
                        json.dumps(a),
                    ),
                )


def upsert_activity_streams(conn, activity_id, streams):
    if not streams or not isinstance(streams, dict):
        print(f"⚠️ No stream payload for activity {activity_id}")
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
            print(f"  -> inserted/updated {inserted_count} stream rows for activity {activity_id}")


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

        print("Recent activity sport types:")
        for a in activities:
            print(f"  id={a.get('id')} sport_type={a.get('sport_type')} name={a.get('name')}")

        print("Connecting to Postgres...")
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD,
        )

        ensure_power_tables(conn)
        upsert_activities(conn, activities)

        # Update Strava strength activity names + descriptions from Hevy data
        update_strength_descriptions(access_token, conn, lookback_days=7)

        ride_candidates = [
            a for a in activities
            if a.get("sport_type") in ("Ride", "VirtualRide", "EBikeRide")
        ]
        print(f"Fetching streams for {len(ride_candidates)} ride activities...")

        for a in ride_candidates:
            activity_id = a.get("id")
            if not activity_id:
                continue
            try:
                streams = fetch_activity_streams(access_token, activity_id)
                print(f"Raw streams response for activity {activity_id}:")
                try:
                    print(json.dumps(streams, indent=2)[:5000])
                except Exception as dump_err:
                    print(f"Could not dump streams for {activity_id}: {dump_err}")

                upsert_activity_streams(conn, activity_id, streams)

                start_time = parse_start_time(a.get("start_date"))
                normalized_rows = normalize_strava_streams(streams, activity_start_time=start_time)

                if normalized_rows:
                    row_count = upsert_activity_stream_rows(conn=conn, activity_id=activity_id, rows=normalized_rows, source="strava")
                    power_values = [row.get("power_w") for row in normalized_rows]
                    best_efforts = upsert_best_efforts(conn=conn, activity_id=activity_id, power_values=power_values, source="strava", windows=[5, 60, 300, 1200])
                    print(f"✅ Power sync complete for activity {activity_id} (rows={row_count}, best_efforts={best_efforts})")
                else:
                    print(f"⚠️ No normalized rows generated for activity {activity_id}")

                print(f"✅ Streams synced for activity {activity_id}")

            except Exception as e:
                print(f"⚠️ Stream sync failed for activity {activity_id}: {e}")

        conn.close()
        print("Strava sync complete.")

    except Exception as e:
        print(f"Strava sync failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
    sys.exit(0)
