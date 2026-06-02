"""
One-time backfill script for Strava rides missing HR/cadence/velocity streams.
Fetches streams for specific activity IDs and upserts into DB.
Run once: docker exec strava-sync python backfill_streams.py
"""
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

# Activity IDs missing HR streams
BACKFILL_IDS = [
    18492726791,  # Lunch Ride 2026-05-13
    18462066871,  # Morning Ride 2026-05-11
    18452214880,  # Morning Ride 2026-05-10
    18428270987,  # Lunch Ride 2026-05-08
]


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


def fetch_activity_streams(access_token, activity_id):
    url = f"https://www.strava.com/api/v3/activities/{activity_id}/streams"
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        params={"keys": ",".join(STREAM_KEYS), "key_by_type": "true"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_activity_detail(access_token, activity_id):
    resp = requests.get(
        f"https://www.strava.com/api/v3/activities/{activity_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def upsert_activity_streams(conn, activity_id, streams):
    if not streams or not isinstance(streams, dict):
        print(f"  No stream payload for {activity_id}")
        return

    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM strava_activity_streams WHERE strava_activity_id = %s",
                (activity_id,),
            )
            inserted = 0
            for stream_type, stream_obj in streams.items():
                data = stream_obj.get("data", []) if isinstance(stream_obj, dict) else []
                for idx, value in enumerate(data):
                    value_numeric = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
                    value_text = value if isinstance(value, str) else None
                    value_json = json.dumps(value) if value_numeric is None and value_text is None else None
                    cur.execute(
                        """
                        INSERT INTO strava_activity_streams
                            (strava_activity_id, stream_type, idx, value_numeric, value_text, value_json, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s::jsonb, now())
                        ON CONFLICT (strava_activity_id, stream_type, idx)
                        DO UPDATE SET
                            value_numeric = EXCLUDED.value_numeric,
                            value_text = EXCLUDED.value_text,
                            value_json = EXCLUDED.value_json,
                            updated_at = now()
                        """,
                        (activity_id, stream_type, idx, value_numeric, value_text, value_json),
                    )
                    inserted += 1
                print(f"    {stream_type}: {len(data)} points")
            print(f"  Total: {inserted} stream rows upserted")


def main():
    print("Loading tokens...")
    tokens = load_tokens()
    new_tokens = refresh_access_token(tokens["refresh_token"])
    save_tokens({
        "access_token": new_tokens["access_token"],
        "refresh_token": new_tokens["refresh_token"],
        "expires_at": new_tokens["expires_at"],
    })
    access_token = new_tokens["access_token"]
    print("Token refreshed.")

    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD
    )
    ensure_power_tables(conn)

    for activity_id in BACKFILL_IDS:
        print(f"\nBackfilling activity {activity_id}...")
        try:
            detail = fetch_activity_detail(access_token, activity_id)
            start_date = detail.get("start_date")
            start_time = None
            if start_date:
                start_time = datetime.datetime.fromisoformat(start_date.replace("Z", "+00:00"))

            streams = fetch_activity_streams(access_token, activity_id)
            upsert_activity_streams(conn, activity_id, streams)

            normalized = normalize_strava_streams(streams, activity_start_time=start_time)
            if normalized:
                row_count = upsert_activity_stream_rows(conn, activity_id, normalized, source="strava")
                power_values = [r.get("power_w") for r in normalized]
                upsert_best_efforts(conn, activity_id, power_values, source="strava", windows=[5, 60, 300, 1200])
                print(f"  Normalized rows: {row_count}")

            print(f"  Done.")
        except Exception as e:
            print(f"  Failed: {e}")

    conn.close()
    print("\nBackfill complete.")


if __name__ == "__main__":
    main()
    sys.exit(0)
