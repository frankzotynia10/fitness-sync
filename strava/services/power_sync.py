from __future__ import annotations

from pathlib import Path
from typing import Any

from services.best_efforts import compute_best_efforts_by_seconds
from services.fit_ingest import parse_fit_records


DEFAULT_BEST_EFFORT_WINDOWS = [5, 60, 300, 1200]


CREATE_ACTIVITY_STREAMS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS activity_streams (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    activity_id BIGINT NOT NULL,
    ts TIMESTAMPTZ NULL,
    time_offset_sec INTEGER NOT NULL,
    power_w INTEGER NULL,
    heart_rate_bpm INTEGER NULL,
    cadence_rpm INTEGER NULL,
    distance_m DOUBLE PRECISION NULL,
    speed_mps DOUBLE PRECISION NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source, activity_id, time_offset_sec)
);
"""


CREATE_ACTIVITY_STREAMS_INDEX_1_SQL = """
CREATE INDEX IF NOT EXISTS idx_activity_streams_activity
    ON activity_streams (activity_id);
"""


CREATE_ACTIVITY_STREAMS_INDEX_2_SQL = """
CREATE INDEX IF NOT EXISTS idx_activity_streams_activity_power
    ON activity_streams (activity_id, power_w);
"""


CREATE_ACTIVITY_BEST_EFFORTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS activity_best_efforts (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    activity_id BIGINT NOT NULL,
    window_sec INTEGER NOT NULL,
    best_avg_power_w DOUBLE PRECISION NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source, activity_id, window_sec)
);
"""


CREATE_ACTIVITY_BEST_EFFORTS_INDEX_1_SQL = """
CREATE INDEX IF NOT EXISTS idx_activity_best_efforts_activity
    ON activity_best_efforts (activity_id);
"""


CREATE_ACTIVITY_BEST_EFFORTS_INDEX_2_SQL = """
CREATE INDEX IF NOT EXISTS idx_activity_best_efforts_window
    ON activity_best_efforts (window_sec, best_avg_power_w DESC);
"""


INSERT_ACTIVITY_STREAM_SQL = """
INSERT INTO activity_streams (
    source,
    activity_id,
    ts,
    time_offset_sec,
    power_w,
    heart_rate_bpm,
    cadence_rpm,
    distance_m,
    speed_mps
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (source, activity_id, time_offset_sec)
DO UPDATE SET
    ts = EXCLUDED.ts,
    power_w = EXCLUDED.power_w,
    heart_rate_bpm = EXCLUDED.heart_rate_bpm,
    cadence_rpm = EXCLUDED.cadence_rpm,
    distance_m = EXCLUDED.distance_m,
    speed_mps = EXCLUDED.speed_mps;
"""


INSERT_ACTIVITY_BEST_EFFORT_SQL = """
INSERT INTO activity_best_efforts (
    source,
    activity_id,
    window_sec,
    best_avg_power_w
)
VALUES (%s, %s, %s, %s)
ON CONFLICT (source, activity_id, window_sec)
DO UPDATE SET
    best_avg_power_w = EXCLUDED.best_avg_power_w,
    computed_at = NOW();
"""


def ensure_power_tables(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(CREATE_ACTIVITY_STREAMS_TABLE_SQL)
        cur.execute(CREATE_ACTIVITY_STREAMS_INDEX_1_SQL)
        cur.execute(CREATE_ACTIVITY_STREAMS_INDEX_2_SQL)

        cur.execute(CREATE_ACTIVITY_BEST_EFFORTS_TABLE_SQL)
        cur.execute(CREATE_ACTIVITY_BEST_EFFORTS_INDEX_1_SQL)
        cur.execute(CREATE_ACTIVITY_BEST_EFFORTS_INDEX_2_SQL)

    conn.commit()


def upsert_activity_stream_rows(
    conn,
    activity_id: int,
    rows: list[dict[str, Any]],
    source: str,
) -> int:
    if not rows:
        return 0

    insert_count = 0

    with conn.cursor() as cur:
        for row in rows:
            cur.execute(
                INSERT_ACTIVITY_STREAM_SQL,
                (
                    source,
                    activity_id,
                    row.get("timestamp"),
                    row.get("time_offset_sec"),
                    row.get("power_w"),
                    row.get("heart_rate_bpm"),
                    row.get("cadence_rpm"),
                    row.get("distance_m"),
                    row.get("speed_mps"),
                ),
            )
            insert_count += 1

    conn.commit()
    return insert_count


def upsert_best_efforts(
    conn,
    activity_id: int,
    power_values: list[int | float | None],
    source: str,
    windows: list[int] | None = None,
) -> dict[int, float | None]:
    if windows is None:
        windows = DEFAULT_BEST_EFFORT_WINDOWS

    efforts = compute_best_efforts_by_seconds(power_values, windows)

    with conn.cursor() as cur:
        for window_sec, best_avg_power_w in efforts.items():
            if best_avg_power_w is None:
                continue

            cur.execute(
                INSERT_ACTIVITY_BEST_EFFORT_SQL,
                (
                    source,
                    activity_id,
                    window_sec,
                    best_avg_power_w,
                ),
            )

    conn.commit()
    return efforts


def ingest_fit_activity(
    conn,
    activity_id: int,
    fit_path: str | Path,
    source: str = "garmin_fit",
    windows: list[int] | None = None,
) -> dict[str, Any]:
    rows = parse_fit_records(fit_path)

    if not rows:
        return {
            "source": source,
            "activity_id": activity_id,
            "stream_rows": 0,
            "best_efforts": {},
            "status": "no_rows",
        }

    stream_rows = upsert_activity_stream_rows(
        conn=conn,
        activity_id=activity_id,
        rows=rows,
        source=source,
    )

    power_values = [row.get("power_w") for row in rows]
    best_efforts = upsert_best_efforts(
        conn=conn,
        activity_id=activity_id,
        power_values=power_values,
        source=source,
        windows=windows,
    )

    return {
        "source": source,
        "activity_id": activity_id,
        "stream_rows": stream_rows,
        "best_efforts": best_efforts,
        "status": "ok",
    }


def has_power_streams(conn, activity_id: int) -> bool:
    sql = """
    SELECT EXISTS (
        SELECT 1
        FROM activity_streams
        WHERE activity_id = %s
          AND power_w IS NOT NULL
    );
    """

    with conn.cursor() as cur:
        cur.execute(sql, (activity_id,))
        result = cur.fetchone()

    return bool(result[0]) if result else False