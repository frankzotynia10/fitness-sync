#!/usr/bin/env python3
"""
hevy2strava.py

Builds a FIT file from today's Hevy workout (from DB) + HR data
(from garmin_hr_intraday) and uploads it directly to Strava.

Bypasses Garmin entirely — no training effect overwrite.

Usage:
  python hevy2strava.py                    # auto: most recent workout today
  python hevy2strava.py --workout-id <id>  # manual: specific hevy workout_id
"""

from __future__ import annotations

import argparse
import datetime
import io
import json
import os
import struct
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

# ── DB ────────────────────────────────────────────────────────────────────────
def get_conn():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD
    )


def fetch_latest_workout(conn) -> dict | None:
    """Return the most recent Hevy workout started today."""
    today = datetime.date.today().isoformat()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT w.workout_id, w.title, w.start_time, w.end_time
            FROM hevy_workouts w
            WHERE DATE(w.start_time AT TIME ZONE 'America/New_York') = %s
            ORDER BY w.start_time DESC
            LIMIT 1
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
            SELECT e.exercise_index, e.title, e.notes,
                   s.set_index, s.set_type, s.weight_kg, s.reps, s.rpe,
                   s.duration_seconds
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
        ex_idx, title, notes, set_idx, set_type, weight_kg, reps, rpe, duration_s = row
        if ex_idx not in exercises:
            exercises[ex_idx] = {"index": ex_idx, "title": title, "notes": notes, "sets": []}
        exercises[ex_idx]["sets"].append({
            "index": set_idx, "type": set_type,
            "weight_kg": weight_kg, "reps": reps,
            "rpe": rpe, "duration_seconds": duration_s,
        })
    return list(exercises.values())


def fetch_hr_data(conn, start_time: datetime.datetime, end_time: datetime.datetime) -> list[tuple]:
    """Return list of (recorded_at, heart_rate) for the workout window."""
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
        return cur.fetchall()


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
# Minimal FIT writer — produces a valid strength_training FIT with:
#   - file_id message
#   - session message
#   - one lap per exercise
#   - HR records for the workout window

FIT_EPOCH = datetime.datetime(1989, 12, 31, 0, 0, 0, tzinfo=datetime.timezone.utc)


def to_fit_timestamp(dt: datetime.datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return int((dt - FIT_EPOCH).total_seconds())


def fit_crc(data: bytes) -> int:
    crc_table = [
        0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
        0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400,
    ]
    crc = 0
    for byte in data:
        tmp = crc_table[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc ^= tmp ^ crc_table[byte & 0xF]
        tmp = crc_table[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc ^= tmp ^ crc_table[(byte >> 4) & 0xF]
    return crc


class FitWriter:
    """Minimal FIT file writer."""

    PROTOCOL_VERSION = 0x10
    PROFILE_VERSION  = 2132

    # Global message numbers
    MESG_FILE_ID  = 0
    MESG_SESSION  = 18
    MESG_LAP      = 19
    MESG_RECORD   = 20
    MESG_ACTIVITY = 34

    # Field defs: (field_def_num, size, base_type)
    # base_type: 0x86=uint32, 0x84=uint16, 0x00=enum, 0x02=uint8, 0x07=string, 0x8B=uint32
    FILE_ID_FIELDS = [
        (0,  1, 0x00),  # type: enum
        (1,  2, 0x84),  # manufacturer: uint16
        (2,  2, 0x84),  # product: uint16
        (4,  4, 0x86),  # time_created: uint32
    ]
    SESSION_FIELDS = [
        (253, 4, 0x86),  # timestamp
        (2,   4, 0x86),  # start_time
        (7,   4, 0x86),  # total_elapsed_time (ms * 1000)
        (8,   4, 0x86),  # total_timer_time
        (0,   1, 0x00),  # event: enum
        (1,   1, 0x00),  # event_type: enum
        (5,   1, 0x00),  # sport: enum (14=training)
        (6,   1, 0x00),  # sub_sport: enum (20=strength)
    ]
    LAP_FIELDS = [
        (253, 4, 0x86),  # timestamp
        (2,   4, 0x86),  # start_time
        (7,   4, 0x86),  # total_elapsed_time
        (0,   1, 0x00),  # event
        (1,   1, 0x00),  # event_type
    ]
    RECORD_FIELDS = [
        (253, 4, 0x86),  # timestamp
        (3,   1, 0x02),  # heart_rate: uint8
    ]
    ACTIVITY_FIELDS = [
        (253, 4, 0x86),  # timestamp
        (1,   4, 0x86),  # total_timer_time
        (2,   2, 0x84),  # num_sessions
        (0,   1, 0x00),  # event
        (1,   1, 0x00),  # event_type (mapped to field 28 in activity)
    ]

    def __init__(self):
        self._buf = io.BytesIO()
        self._local_msg_types: dict[int, int] = {}  # global_mesg_num -> local_mesg_num
        self._next_local = 0
        self._data_records: list[bytes] = []

    def _define_message(self, global_mesg_num: int, fields: list) -> int:
        local_num = self._next_local
        self._next_local += 1
        self._local_msg_types[global_mesg_num] = local_num

        field_bytes = b""
        for fdef_num, size, base_type in fields:
            field_bytes += struct.pack("BBB", fdef_num, size, base_type)

        # Definition message header: 0x40 | local_num
        header = 0x40 | local_num
        body = struct.pack(">BHB", 0, global_mesg_num, len(fields)) + field_bytes
        record = struct.pack("B", header) + body
        self._buf.write(record)
        return local_num

    def _write_data(self, global_mesg_num: int, values: list):
        local_num = self._local_msg_types[global_mesg_num]
        header = local_num & 0x0F
        body = b""
        for v, (_, size, base_type) in zip(values, self._get_fields(global_mesg_num)):
            if size == 4:
                body += struct.pack("<I", int(v) & 0xFFFFFFFF)
            elif size == 2:
                body += struct.pack("<H", int(v) & 0xFFFF)
            else:
                body += struct.pack("B", int(v) & 0xFF)
        self._buf.write(struct.pack("B", header) + body)

    def _get_fields(self, global_mesg_num: int):
        return {
            self.MESG_FILE_ID:  self.FILE_ID_FIELDS,
            self.MESG_SESSION:  self.SESSION_FIELDS,
            self.MESG_LAP:      self.LAP_FIELDS,
            self.MESG_RECORD:   self.RECORD_FIELDS,
            self.MESG_ACTIVITY: self.ACTIVITY_FIELDS,
        }[global_mesg_num]

    def build(self,
              workout: dict,
              exercises: list[dict],
              hr_data: list[tuple]) -> bytes:

        start_ts = to_fit_timestamp(workout["start_time"])
        end_ts   = to_fit_timestamp(workout["end_time"])
        elapsed  = end_ts - start_ts
        elapsed_ms = elapsed * 1000

        # File ID
        self._define_message(self.MESG_FILE_ID, self.FILE_ID_FIELDS)
        self._write_data(self.MESG_FILE_ID, [
            4,      # type: activity
            255,    # manufacturer: development
            0,      # product
            start_ts,
        ])

        # HR records
        if hr_data:
            self._define_message(self.MESG_RECORD, self.RECORD_FIELDS)
            for recorded_at, hr in hr_data:
                if hr is None:
                    continue
                ts = to_fit_timestamp(recorded_at)
                self._write_data(self.MESG_RECORD, [ts, int(hr)])

        # One lap per exercise
        self._define_message(self.MESG_LAP, self.LAP_FIELDS)
        num_exercises = len(exercises)
        if num_exercises > 0:
            lap_duration = elapsed // max(num_exercises, 1)
            for i, exercise in enumerate(exercises):
                lap_start = start_ts + (i * lap_duration)
                lap_end   = lap_start + lap_duration
                self._write_data(self.MESG_LAP, [
                    lap_end,        # timestamp
                    lap_start,      # start_time
                    lap_duration * 1000,  # total_elapsed_time
                    9,              # event: lap
                    1,              # event_type: stop
                ])

        # Session
        self._define_message(self.MESG_SESSION, self.SESSION_FIELDS)
        self._write_data(self.MESG_SESSION, [
            end_ts,
            start_ts,
            elapsed_ms,
            elapsed_ms,
            9,   # event: lap
            1,   # event_type: stop
            14,  # sport: training
            20,  # sub_sport: strength_training
        ])

        # Activity
        self._define_message(self.MESG_ACTIVITY, self.ACTIVITY_FIELDS)
        self._write_data(self.MESG_ACTIVITY, [
            end_ts,
            elapsed_ms,
            1,   # num_sessions
            26,  # event: activity
            1,   # event_type: stop
        ])

        data = self._buf.getvalue()

        # FIT file header: 14 bytes
        data_size = len(data)
        header = struct.pack(
            "<BBHI4s",
            14,                      # header size
            self.PROTOCOL_VERSION,
            self.PROFILE_VERSION,
            data_size,
            b".FIT"
        )
        header_crc = fit_crc(header)
        header += struct.pack("<H", header_crc)

        body = header + data
        data_crc = fit_crc(data)
        return body + struct.pack("<H", data_crc)


# ── Strava upload ─────────────────────────────────────────────────────────────
def upload_to_strava(fit_bytes: bytes, activity_name: str, access_token: str) -> int | None:
    resp = requests.post(
        "https://www.strava.com/api/v3/uploads",
        headers={"Authorization": f"Bearer {access_token}"},
        data={
            "data_type":  "fit",
            "name":       activity_name,
            "sport_type": "WeightTraining",
            "trainer":    "1",
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
        error = poll.get("error", "")
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
    print(f"HR data points: {len(hr_data)}")

    print("Building FIT file...")
    writer = FitWriter()
    fit_bytes = writer.build(workout, exercises, hr_data)
    print(f"FIT size: {len(fit_bytes)} bytes")

    print("Getting Strava token...")
    access_token = get_access_token()

    print("Uploading to Strava...")
    activity_id = upload_to_strava(fit_bytes, workout["title"], access_token)
    if activity_id:
        print(f"Done: https://www.strava.com/activities/{activity_id}")
    else:
        print("Skipped (duplicate).")

    conn.close()


if __name__ == "__main__":
    main()
    sys.exit(0)
