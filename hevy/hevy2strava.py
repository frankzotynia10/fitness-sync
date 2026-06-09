#!/usr/bin/env python3
"""
hevy2strava.py

Builds a Strava JSON upload from today's Hevy workout (from DB) + HR data
(from garmin_activity_gps, falling back to garmin_hr_intraday).

Uses Strava's JSON upload format which supports sets, reps, weight, and
HR streams in a single file — giving full exercise data AND heart rate.

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

# ── Exercise name mapping: Hevy title → Strava exercise_type ─────────────────
EXERCISE_MAP = {
    # Squat
    "Squat (Barbell)":              "BARBELL_BACK_SQUAT",
    "Hack Squat (Machine)":         "HACK_SQUAT",
    "Leg Press (Machine)":          "LEG_PRESS",

    # Deadlift
    "Deadlift (Trap bar)":          "TRAP_BAR_DEADLIFT",
    "Romanian Deadlift (Barbell)":  "BARBELL_STRAIGHT_LEG_DEADLIFT",

    # Bench / Chest
    "Bench Press (Barbell)":        "BARBELL_BENCH_PRESS",
    "Incline Bench Press (Barbell)":"INCLINE_BARBELL_BENCH_PRESS",

    # Row / Back
    "Bent Over Row (Barbell)":      "BARBELL_ROW",
    "Seated Cable Row - Bar Grip":  "SEATED_CABLE_ROW",
    "Reverse Grip Lat Pulldown (Cable)": "CABLE_LAT_PULLDOWN",
    "Reverse Fly Single Arm (Cable)":    "CABLE_REVERSE_FLYЕ",

    # Pull / Chin
    "Pull Up (Weighted)":           "PULL_UP",
    "Chin Up (Weighted)":           "CHIN_UP",

    # Shoulder
    "Overhead Press (Barbell)":     "BARBELL_SHOULDER_PRESS",
    "Seated Shoulder Press (Machine)": "MACHINE_SHOULDER_PRESS",
    "Lateral Raise (Dumbbell)":     "DUMBBELL_LATERAL_RAISE",
    "Single Arm Lateral Raise (Cable)": "CABLE_LATERAL_RAISE",

    # Curl / Bicep
    "Bicep Curl (Cable)":           "CABLE_BICEPS_CURL",
    "Hammer Curl (Cable)":          "CABLE_HAMMER_CURL",
    "Behind the Back Curl (Cable)": "CABLE_BICEPS_CURL",

    # Tricep
    "Overhead Triceps Extension (Cable)": "CABLE_OVERHEAD_TRICEPS_EXTENSION",
    "Triceps Extension (Machine)":   "MACHINE_TRICEPS_EXTENSION",

    # Leg / Hamstring
    "Lying Leg Curl (Machine)":     "LYING_LEG_CURL",
    "Seated Leg Curl (Machine)":    "SEATED_LEG_CURL",
    "Single Leg Extensions":        "LEG_EXTENSION",

    # Core / Ab
    "Decline Crunch (Weighted)":    "DECLINE_CRUNCH",
    "Leg Raise Parallel Bars":      "HANGING_LEG_RAISE",
}

FALLBACK_EXERCISE_TYPE = "WORKOUT_GENERIC"


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
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT recorded_at, heart_rate
            FROM garmin_activity_gps
            WHERE recorded_at >= %s AND recorded_at <= %s AND heart_rate IS NOT NULL
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


# ── JSON payload builder ──────────────────────────────────────────────────────
def build_json_payload(workout: dict, exercises: list[dict], hr_data: list[tuple]) -> str:
    start_dt = workout["start_time"]
    end_dt   = workout["end_time"]
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=datetime.timezone.utc)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=datetime.timezone.utc)

    elapsed_s = int((end_dt - start_dt).total_seconds())

    sets = []
    for exercise in exercises:
        exercise_type = EXERCISE_MAP.get(exercise["title"])
        if not exercise_type:
            print(f"  WARNING: No mapping for '{exercise['title']}' — using {FALLBACK_EXERCISE_TYPE}")
            exercise_type = FALLBACK_EXERCISE_TYPE
        else:
            print(f"  Mapped '{exercise['title']}' → {exercise_type}")

        for s in exercise["sets"]:
            entry = {"exercise_type": exercise_type}
            if s["reps"] is not None:
                entry["repetitions"] = int(s["reps"])
            if s["weight_kg"] is not None:
                entry["weight"] = float(s["weight_kg"])
            if s["duration_seconds"] is not None:
                entry["duration"] = int(s["duration_seconds"])
            sets.append(entry)

    payload: dict = {
        "version":      "1.0",
        "start_time":   start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "utc_offset":   0,
        "elapsed_time": elapsed_s,
        "active_time":  elapsed_s,
        "creator":      {"name": "Mayfair Labs hevy2strava"},
        "sets":         sets,
    }

    if hr_data:
        time_offsets = []
        hr_values    = []
        for recorded_at, hr in hr_data:
            if hr is None:
                continue
            if recorded_at.tzinfo is None:
                recorded_at = recorded_at.replace(tzinfo=datetime.timezone.utc)
            offset = int((recorded_at - start_dt).total_seconds())
            if 0 <= offset <= elapsed_s:
                time_offsets.append(offset)
                hr_values.append(int(hr))

        if time_offsets:
            payload["streams"] = {
                "time":      time_offsets,
                "heartrate": hr_values,
            }
            print(f"  HR stream: {len(hr_values)} points")

    return json.dumps(payload)


# ── Strava upload ─────────────────────────────────────────────────────────────
def upload_to_strava(json_payload: str, activity_name: str, workout_id: str, access_token: str) -> int | None:
    resp = requests.post(
        "https://www.strava.com/api/v3/uploads",
        headers={"Authorization": f"Bearer {access_token}"},
        data={
            "data_type":   "json",
            "name":        activity_name,
            "sport_type":  "WeightTraining",
            "trainer":     "1",
            "external_id": f"hevy-{workout_id}",
        },
        files={"file": ("workout.json", json_payload.encode(), "application/json")},
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
    parser = argparse.ArgumentParser(description="Build Strava JSON from Hevy DB + upload")
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
            # No workout today — not an error, just nothing to do
            print("No workout found for today — skipping.")
            sys.exit(0)

    print(f"Workout: {workout['title']} | {workout['start_time']} → {workout['end_time']}")

    exercises = fetch_exercises(conn, workout["workout_id"])
    print(f"Exercises: {len(exercises)}")

    hr_data = fetch_hr_data(conn, workout["start_time"], workout["end_time"])

    print("Building JSON payload...")
    json_payload = build_json_payload(workout, exercises, hr_data)
    print(f"Payload size: {len(json_payload)} bytes")

    print("Getting Strava token...")
    access_token = get_access_token()

    print("Uploading to Strava...")
    activity_id = upload_to_strava(json_payload, workout["title"], workout["workout_id"], access_token)
    if activity_id:
        print(f"Done: https://www.strava.com/activities/{activity_id}")
    else:
        print("Skipped (duplicate).")

    conn.close()


if __name__ == "__main__":
    main()
    sys.exit(0)
