#!/usr/bin/env python3
"""
hevy2strava.py

Builds a FIT file from today's Hevy workout (from DB) + HR data
(from garmin_activity_gps, falling back to garmin_hr_intraday)
and uploads it directly to Strava.

Bypasses Garmin entirely — no training effect overwrite.

Usage:
  python hevy2strava.py                    # auto: most recent workout today
  python hevy2strava.py --workout-id <id>  # manual: specific hevy workout_id
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time
import requests
import psycopg2
from dotenv import load_dotenv

from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.activity_message import ActivityMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.profile_type import (
    FileType, Manufacturer, Sport, SubSport, Activity, Event, EventType
)

load_dotenv()

# ── Env ───────────────────────────────────────────────────────────────────────
STRAVA_CLIENT_ID     = os.environ["STRAVA_CLIENT_ID"]
STRAVA_CLIENT_SECRET = os.environ["STRAVA_CLIENT_SECRET"]
STRAVA_TOKENS_FILE   = os.environ["STRAVA_TOKENS_FILE"]
DB_HOST              = os.environ["DB_HOST"]
DB_PORT              = os.environ.get("DB_PORT", "5432")
DB_NAME              = os.environ.get("DB_NAME", "postgres")
DB_USER              = os.environ.get("DB_USER", "postgres")
DB_PASSWORD          = os.environ["DB_PASSWORD"]

FIT_EPOCH = datetime.datetime(1989, 12, 31, 0, 0, 0, tzinfo=datetime.timezone.utc)


def to_fit_ts_ms(dt: datetime.datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return int((dt - FIT_EPOCH).total_seconds() * 1000)


# ── DB ────────────────────────────────────────────────────────────────────────
def get_conn():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD
    )


def fetch_latest_workout(conn) -> dict | None:
    today = datetime.date.today().isoformat()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT workout_id, title, start_time, end_time
            FROM hevy_workouts
            WHERE DATE(start_time AT TIME ZONE 'America/New_York') = %s
            ORDER BY start_time DESC LIMIT 1
            """,
            (today,)
        )
        row = cur.fetchone()
    if not row:
        return None
    return {"workout_id": row[0], "title": row[1], "start_time": row[2], "end_time": row[3]}


def fetch_workout_by_id(conn, workout_id: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT workout_id, title, start_time, end_time FROM hevy_workouts WHERE workout_id = %s",
            (workout_id,)
        )
        row = cur.fetchone()
    if not row:
        return None
    return {"workout_id": row[0], "title": row[1], "start_time": row[2], "end_time": row[3]}


def fetch_exercises(conn, workout_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.exercise_index, e.title,
                   s.set_index, s.weight_kg, s.reps, s.duration_seconds
            FROM hevy_workout_exercises e
            JOIN hevy_workout_sets s
              ON s.workout_id = e.workout_id AND s.exercise_index = e.exercise_index
            WHERE e.workout_id = %s
            ORDER BY e.exercise_index, s.set_index
            """,
            (workout_id,)
        )
        rows = cur.fetchall()

    exercises: dict[int, dict] = {}
    for row in rows:
        ex_idx, title, set_idx, weight_kg, reps, duration_s = row
        if ex_idx not in exercises:
            exercises[ex_idx] = {"index": ex_idx, "title": title, "sets": []}
        exercises[ex_idx]["sets"].append({
            "index": set_idx, "weight_kg": weight_kg,
            "reps": reps, "duration_seconds": duration_s,
        })
    return list(exercises.values())


def fetch_hr_data(conn, start_time: datetime.datetime, end_time: datetime.datetime) -> list[tuple]:
    """
    Prefer per-second HR from garmin_activity_gps (captured from FIT file).
    Fall back to garmin_hr_intraday if no activity GPS data exists.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT recorded_at, heart_rate
            FROM garmin_activity_gps
            WHERE recorded_at >= %s
              AND recorded_at <= %s
              AND heart_rate IS NOT NULL
            ORDER BY recorded_at
            """,
            (start_time, end_time)
        )
        rows = cur.fetchall()

    if rows:
        print(f"  HR source: garmin_activity_gps ({len(rows)} points)")
        return rows

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT recorded_at, heart_rate
            FROM garmin_hr_intraday
            WHERE recorded_at >= %s AND recorded_at <= %s
            ORDER BY recorded_at
            """,
            (start_time, end_time)
        )
        rows = cur.fetchall()

    print(f"  HR source: garmin_hr_intraday ({len(rows)} points)")
    return rows


# ── Strava token helpers ──────────────────────────────────────────────────────
def load_tokens() -> dict:
    with open(STRAVA_TOKENS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_tokens(data: dict) -> None:
    tmp = STRAVA_TOKENS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, STRAVA_TOKENS_FILE)


def get_access_token() -> str:
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
    print("  Token refreshed.")
    return new["access_token"]


# ── FIT file builder ──────────────────────────────────────────────────────────
def build_fit(workout: dict, exercises: list[dict], hr_data: list[tuple]) -> bytes:
    builder = FitFileBuilder(auto_define=True, min_string_size=50)

    start_dt = workout["start_time"]
    end_dt   = workout["end_time"]
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=datetime.timezone.utc)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=datetime.timezone.utc)

    start_ms   = to_fit_ts_ms(start_dt)
    end_ms     = to_fit_ts_ms(end_dt)
    elapsed_ms = end_ms - start_ms

    # file_id
    msg = FileIdMessage()
    msg.type = FileType.ACTIVITY
    msg.manufacturer = Manufacturer.DEVELOPMENT
    msg.product = 0
    msg.time_created = start_ms
    builder.add(msg)

    # HR records
    for recorded_at, hr in hr_data:
        if hr is None:
            continue
        if recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=datetime.timezone.utc)
        rec = RecordMessage()
        rec.timestamp  = to_fit_ts_ms(recorded_at)
        rec.heart_rate = int(hr)
        builder.add(rec)

    # One lap per exercise
    num_ex = max(len(exercises), 1)
    lap_ms = elapsed_ms // num_ex
    for i, exercise in enumerate(exercises):
        lap_start_ms = start_ms + i * lap_ms
        lap_end_ms   = lap_start_ms + lap_ms
        lap = LapMessage()
        lap.timestamp          = lap_end_ms
        lap.start_time         = lap_start_ms
        lap.total_elapsed_time = lap_ms / 1000.0
        lap.total_timer_time   = lap_ms / 1000.0
        lap.event              = Event.LAP
        lap.event_type         = EventType.STOP
        lap.sport              = Sport.TRAINING
        lap.sub_sport          = SubSport.STRENGTH_TRAINING
        builder.add(lap)

    # session
    session = SessionMessage()
    session.timestamp          = end_ms
    session.start_time         = start_ms
    session.total_elapsed_time = elapsed_ms / 1000.0
    session.total_timer_time   = elapsed_ms / 1000.0
    session.sport              = Sport.TRAINING
    session.sub_sport          = SubSport.STRENGTH_TRAINING
    session.event              = Event.SESSION
    session.event_type         = EventType.STOP
    session.first_lap_index    = 0
    session.num_laps           = len(exercises)
    builder.add(session)

    # activity
    activity = ActivityMessage()
    activity.timestamp        = end_ms
    activity.total_timer_time = elapsed_ms / 1000.0
    activity.num_sessions     = 1
    activity.type             = Activity.MANUAL
    activity.event            = Event.ACTIVITY
    activity.event_type       = EventType.STOP
    builder.add(activity)

    fit_file = builder.build()
    return fit_file.to_bytes()


# ── Strava upload ─────────────────────────────────────────────────────────────
def upload_to_strava(fit_bytes: bytes, activity_name: str, workout_id: str, access_token: str) -> int | None:
    resp = requests.post(
        "https://www.strava.com/api/v3/uploads",
        headers={"Authorization": f"Bearer {access_token}"},
        data={
            "data_type":   "fit",
            "name":        activity_name,
            "sport_type":  "WeightTraining",
            "trainer":     "1",
            "external_id": f"hevy-{workout_id}",  # unique per workout — bypasses content dedup
        },
        files={"file": ("workout.fit", fit_bytes, "application/octet-stream")},
        timeout=30,
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Upload failed [{resp.status_code}]: {resp.text}")
    upload_id = resp.json().get("id")
    print(f"  Upload queued: id={upload_id}")

    for _ in range(12):
        time.sleep(5)
        poll = requests.get(
            f"https://www.strava.com/api/v3/uploads/{upload_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        ).json()
        error       = poll.get("error", "")
        activity_id = poll.get("activity_id")
        if error and "duplicate" in error.lower():
            print("  Duplicate — already on Strava.")
            return None
        if error:
            raise RuntimeError(f"Upload error: {error}")
        if activity_id:
            return activity_id

    raise RuntimeError("Timed out waiting for Strava upload")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Build FIT from Hevy DB + upload to Strava")
    parser.add_argument("--workout-id", type=str, help="Specific Hevy workout ID")
    args = parser.parse_args()

    conn = get_conn()
    print("Connected to DB.")

    if args.workout_id:
        workout = fetch_workout_by_id(conn, args.workout_id)
        if not workout:
            print(f"Workout {args.workout_id} not found.", file=sys.stderr)
            sys.exit(1)
    else:
        workout = fetch_latest_workout(conn)
        if not workout:
            print("No workout found for today.", file=sys.stderr)
            sys.exit(1)

    print(f"Workout: {workout['title']} | {workout['start_time']} → {workout['end_time']}")

    exercises = fetch_exercises(conn, workout["workout_id"])
    print(f"Exercises: {len(exercises)}")

    hr_data = fetch_hr_data(conn, workout["start_time"], workout["end_time"])

    print("Building FIT file...")
    fit_bytes = build_fit(workout, exercises, hr_data)
    print(f"FIT size: {len(fit_bytes)} bytes")

    print("Getting Strava token...")
    access_token = get_access_token()

    print("Uploading to Strava...")
    activity_id = upload_to_strava(fit_bytes, workout["title"], workout["workout_id"], access_token)
    if activity_id:
        print(f"Done: https://www.strava.com/activities/{activity_id}")
    else:
        print("Skipped (duplicate).")

    conn.close()


if __name__ == "__main__":
    main()
    sys.exit(0)
