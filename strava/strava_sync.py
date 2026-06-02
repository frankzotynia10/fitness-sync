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
    params = {
        "keys": ",".join(keys),
        "key_by_type": "true",
    }

    resp = requests.get(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def upsert_activities(conn, activities):
    with conn:
        with conn.cursor() as cur:
            for a in activities:
                cur.execute(
                    """
                    INSERT INTO strava_activities (
                        strava_activity_id,
                        activity_date,
                        name,
                        sport_type,
                        distance_m,
                        moving_time_s,
                        elapsed_time_s,
                        total_elevation_gain_m,
                        average_speed,
                        max_speed,
                        average_heartrate,
                        max_heartrate,
                        average_watts,
                        weighted_average_watts,
                        max_watts,
                        kilojoules,
                        trainer,
                        commute,
                        manual,
                        private,
                        raw_json,
                        updated_at
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
                        a.get("id"),
                        a.get("start_date"),
                        a.get("name"),
                        a.get("sport_type"),
                        a.get("distance"),
                        a.get("moving_time"),
                        a.get("elapsed_time"),
                        a.get("total_elevation_gain"),
                        a.get("average_speed"),
                        a.get("max_speed"),
                        a.get("average_heartrate"),
                        a.get("max_heartrate"),
                        a.get("average_watts"),
                        a.get("weighted_average_watts"),
                        a.get("max_watts"),
                        a.get("kilojoules"),
                        a.get("trainer"),
                        a.get("commute"),
                        a.get("manual"),
                        a.get("private"),
                        json.dumps(a),
                    ),
                )


def upsert_activity_streams(conn, activity_id, streams):
    if not streams or not isinstance(streams, dict):
        print(f"⚠️ No stream payload for activity {activity_id}")
        return

    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "delete from strava_activity_streams where strava_activity_id = %s",
                (activity_id,),
            )

            inserted_count = 0

            for stream_type, stream_obj in streams.items():
                if not isinstance(stream_obj, dict):
                    print(
                        f"⚠️ Unexpected stream object for {activity_id} / {stream_type}: "
                        f"{type(stream_obj)}"
                    )
                    continue

                data = stream_obj.get("data", [])
                if not isinstance(data, list):
                    print(f"⚠️ Stream {stream_type} for {activity_id} has non-list data")
                    continue

                print(f"  -> stream_type={stream_type}, points={len(data)}")

                for idx, value in enumerate(data):
                    value_numeric = None
                    value_text = None
                    value_json = None

                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        value_numeric = float(value)
                    elif isinstance(value, str):
                        value_text = value
                    else:
                        value_json = json.dumps(value)

                    cur.execute(
                        """
                        insert into strava_activity_streams (
                            strava_activity_id,
                            stream_type,
                            idx,
                            value_numeric,
                            value_text,
                            value_json,
                            updated_at
                        )
                        values (%s, %s, %s, %s, %s, %s::jsonb, now())
                        on conflict (strava_activity_id, stream_type, idx)
                        do update set
                            value_numeric = excluded.value_numeric,
                            value_text = excluded.value_text,
                            value_json = excluded.value_json,
                            updated_at = now();
                        """,
                        (
                            activity_id,
                            stream_type,
                            idx,
                            value_numeric,
                            value_text,
                            value_json,
                        ),
                    )
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

        save_tokens(
            {
                "access_token": new_tokens["access_token"],
                "refresh_token": new_tokens["refresh_token"],
                "expires_at": new_tokens["expires_at"],
            }
        )

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
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
        )

        ensure_power_tables(conn)

        upsert_activities(conn, activities)

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
                normalized_rows = normalize_strava_streams(
                    streams,
                    activity_start_time=start_time,
                )

                if normalized_rows:
                    row_count = upsert_activity_stream_rows(
                        conn=conn,
                        activity_id=activity_id,
                        rows=normalized_rows,
                        source="strava",
                    )

                    power_values = [row.get("power_w") for row in normalized_rows]

                    best_efforts = upsert_best_efforts(
                        conn=conn,
                        activity_id=activity_id,
                        power_values=power_values,
                        source="strava",
                        windows=[5, 60, 300, 1200],
                    )

                    print(
                        f"✅ Power sync complete for activity {activity_id} "
                        f"(rows={row_count}, best_efforts={best_efforts})"
                    )
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
