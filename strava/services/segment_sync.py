"""
services/segment_sync.py
Handles fetching and upserting Strava segment efforts.
"""

import time
import requests


RATE_LIMIT_DELAY = 1.0  # seconds between detailed activity fetches


def fetch_activity_detail(access_token: str, activity_id: int) -> dict:
    """Fetch full activity detail including segment_efforts."""
    url = f"https://www.strava.com/api/v3/activities/{activity_id}"
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ensure_segment_tables(conn) -> None:
    """Create strava_segment_efforts table if it doesn't exist."""
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS strava_segment_efforts (
                    id                 BIGINT PRIMARY KEY,
                    activity_id        BIGINT,
                    segment_id         BIGINT,
                    segment_name       TEXT,
                    elapsed_time_s     INT,
                    moving_time_s      INT,
                    start_date         TIMESTAMPTZ,
                    distance_m         FLOAT,
                    average_watts      FLOAT,
                    average_heartrate  FLOAT,
                    pr_rank            INT,
                    kom_rank           INT,
                    updated_at         TIMESTAMPTZ DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_seg_efforts_activity_id
                    ON strava_segment_efforts (activity_id);
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_seg_efforts_segment_id
                    ON strava_segment_efforts (segment_id);
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_seg_efforts_start_date
                    ON strava_segment_efforts (start_date);
            """)


def upsert_segment_efforts(conn, activity_id: int, segment_efforts: list) -> int:
    """
    Upsert segment efforts for a single activity.
    Returns count of rows upserted.
    """
    if not segment_efforts:
        return 0

    count = 0
    with conn:
        with conn.cursor() as cur:
            for effort in segment_efforts:
                segment = effort.get("segment", {})
                cur.execute("""
                    INSERT INTO strava_segment_efforts (
                        id, activity_id, segment_id, segment_name,
                        elapsed_time_s, moving_time_s, start_date,
                        distance_m, average_watts, average_heartrate,
                        pr_rank, kom_rank, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW()
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        elapsed_time_s    = EXCLUDED.elapsed_time_s,
                        moving_time_s     = EXCLUDED.moving_time_s,
                        average_watts     = EXCLUDED.average_watts,
                        average_heartrate = EXCLUDED.average_heartrate,
                        pr_rank           = EXCLUDED.pr_rank,
                        kom_rank          = EXCLUDED.kom_rank,
                        updated_at        = NOW()
                """, (
                    effort.get("id"),
                    activity_id,
                    segment.get("id"),
                    segment.get("name"),
                    effort.get("elapsed_time"),
                    effort.get("moving_time"),
                    effort.get("start_date"),
                    effort.get("distance"),
                    effort.get("average_watts"),
                    effort.get("average_heartrate"),
                    effort.get("pr_rank"),
                    effort.get("kom_rank"),
                ))
                count += 1
    return count


def sync_segments_for_activities(
    conn,
    access_token: str,
    activity_ids: list[int],
    delay: float = RATE_LIMIT_DELAY,
    log_prefix: str = "",
) -> dict:
    """
    Fetch detailed activity and upsert segment efforts for a list of activity IDs.
    Returns summary dict with counts.
    """
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
            print(f"{log_prefix}[{i+1}/{len(activity_ids)}] activity {activity_id}: {count} segment efforts upserted")
        except Exception as e:
            failed += 1
            print(f"{log_prefix}[{i+1}/{len(activity_ids)}] activity {activity_id}: FAILED — {e}")

        if i < len(activity_ids) - 1:
            time.sleep(delay)

    return {"success": success, "failed": failed, "total_efforts": total_efforts}
