"""
backfill_segments.py

Backfills segment efforts for all historical ride activities not yet in
strava_segment_efforts. Rate-limit aware — stays well within Strava's
100 req/15min cap. Run manually or schedule off-peak.

Usage:
    python backfill_segments.py               # dry run — shows what would be processed
    python backfill_segments.py --run         # actually execute
    python backfill_segments.py --run --limit 50   # process at most 50 activities
"""

import os
import sys
import json
import time
import argparse
import psycopg2
import requests
from dotenv import load_dotenv

from services.segment_sync import ensure_segment_tables, upsert_segment_efforts

load_dotenv()

STRAVA_CLIENT_ID = os.environ["STRAVA_CLIENT_ID"]
STRAVA_CLIENT_SECRET = os.environ["STRAVA_CLIENT_SECRET"]
STRAVA_TOKENS_FILE = os.environ["STRAVA_TOKENS_FILE"]

DB_HOST = os.environ["DB_HOST"]
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "postgres")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ["DB_PASSWORD"]

# Stay comfortably under 100 req/15min (6.6/min).
# Each activity = 1 detail request. 10s delay = ~6/min.
DELAY_BETWEEN_REQUESTS = 10.0

RIDE_SPORT_TYPES = ("Ride", "VirtualRide", "EBikeRide")


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


def fetch_activity_detail(access_token: str, activity_id: int) -> dict:
    url = f"https://www.strava.com/api/v3/activities/{activity_id}"
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_unsynced_ride_ids(conn, limit: int | None = None) -> list[int]:
    """
    Return activity IDs for rides that have no rows in strava_segment_efforts yet.
    Ordered oldest-first so backfill progresses chronologically.
    """
    query = """
        SELECT sa.strava_activity_id
        FROM strava_activities sa
        WHERE sa.sport_type = ANY(%s)
          AND NOT EXISTS (
              SELECT 1 FROM strava_segment_efforts sse
              WHERE sse.activity_id = sa.strava_activity_id
          )
        ORDER BY sa.activity_date ASC
    """
    params: list = [list(RIDE_SPORT_TYPES)]

    if limit:
        query += " LIMIT %s"
        params.append(limit)

    with conn.cursor() as cur:
        cur.execute(query, params)
        return [row[0] for row in cur.fetchall()]


def get_total_ride_count(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM strava_activities WHERE sport_type = ANY(%s)",
            (list(RIDE_SPORT_TYPES),)
        )
        return cur.fetchone()[0]


def get_synced_ride_count(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(DISTINCT activity_id)
            FROM strava_segment_efforts
        """)
        return cur.fetchone()[0]


def main():
    parser = argparse.ArgumentParser(description="Backfill Strava segment efforts")
    parser.add_argument("--run", action="store_true", help="Actually execute (default is dry run)")
    parser.add_argument("--limit", type=int, default=None, help="Max activities to process")
    args = parser.parse_args()

    print("Connecting to Postgres...")
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD,
    )

    ensure_segment_tables(conn)

    total_rides = get_total_ride_count(conn)
    synced_rides = get_synced_ride_count(conn)
    print(f"Rides in DB: {total_rides} total, {synced_rides} already have segment data")

    activity_ids = get_unsynced_ride_ids(conn, limit=args.limit)
    print(f"Activities to process: {len(activity_ids)}")

    if not activity_ids:
        print("Nothing to backfill.")
        conn.close()
        return

    estimated_time_min = (len(activity_ids) * DELAY_BETWEEN_REQUESTS) / 60
    print(f"Estimated time at {DELAY_BETWEEN_REQUESTS}s/request: ~{estimated_time_min:.1f} minutes")

    if not args.run:
        print("\nDry run — pass --run to execute.")
        conn.close()
        return

    print("\nRefreshing Strava token...")
    tokens = load_tokens()
    new_tokens = refresh_access_token(tokens["refresh_token"])
    save_tokens({
        "access_token": new_tokens["access_token"],
        "refresh_token": new_tokens["refresh_token"],
        "expires_at": new_tokens["expires_at"],
    })
    access_token = new_tokens["access_token"]
    print("✅ Token refreshed\n")

    total_efforts = 0
    success = 0
    failed = 0

    for i, activity_id in enumerate(activity_ids):
        try:
            detail = fetch_activity_detail(access_token, activity_id)
            efforts = detail.get("segment_efforts", [])
            count = upsert_segment_efforts(conn, activity_id, efforts)
            total_efforts += count
            success += 1
            print(f"[{i+1}/{len(activity_ids)}] activity {activity_id}: {count} segment efforts")
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else "?"
            if status == 429:
                print(f"[{i+1}/{len(activity_ids)}] Rate limited (429) — sleeping 60s...")
                time.sleep(60)
                # Retry once
                try:
                    detail = fetch_activity_detail(access_token, activity_id)
                    efforts = detail.get("segment_efforts", [])
                    count = upsert_segment_efforts(conn, activity_id, efforts)
                    total_efforts += count
                    success += 1
                    print(f"  Retry OK: {count} segment efforts")
                except Exception as retry_err:
                    failed += 1
                    print(f"  Retry failed: {retry_err}")
            else:
                failed += 1
                print(f"[{i+1}/{len(activity_ids)}] activity {activity_id}: HTTP {status} — skipping")
        except Exception as e:
            failed += 1
            print(f"[{i+1}/{len(activity_ids)}] activity {activity_id}: FAILED — {e}")

        if i < len(activity_ids) - 1:
            time.sleep(DELAY_BETWEEN_REQUESTS)

    conn.close()
    print(f"\n{'='*50}")
    print(f"Backfill complete")
    print(f"  Activities processed: {success} ok, {failed} failed")
    print(f"  Total segment efforts upserted: {total_efforts}")


if __name__ == "__main__":
    main()
