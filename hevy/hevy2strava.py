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

FIT_EPOCH = datetime.datetime(1989, 12, 31, 0, 0, 0, tzinfo=datetime.timezone.utc)


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
def to_fit_ts(dt: datetime.datetime) -> int:
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


def build_fit(workout: dict, exercises: list[dict], hr_data: list[tuple]) -> bytes:
    """
    Build a minimal but valid FIT activity file.

    FIT binary layout:
      [14-byte file header] [definition messages + data messages] [2-byte CRC]

    Definition message format:
      Byte 0:   header  = 0x40 | local_mesg_num
      Byte 1:   reserved = 0x00
      Byte 2:   arch     = 0x00 (little-endian)
      Bytes 3-4: global_mesg_num (uint16 LE)
      Byte 5:   num_fields
      Then for each field: [field_def_num, size, base_type_num] (3 bytes each)

    Data message format:
      Byte 0: local_mesg_num
      Then field values in little-endian order matching the definition
    """

    buf = bytearray()

    # local message number counter
    local = [0]

    def define(global_num: int, fields: list[tuple]) -> int:
        """Write a definition message, return local_num."""
        lnum = local[0]
        local[0] += 1
        field_bytes = b"".join(struct.pack("BBB", f, s, t) for f, s, t in fields)
        hdr = struct.pack("B", 0x40 | lnum)
        body = struct.pack("<BHB", 0x00, global_num, len(fields)) + field_bytes
        buf.extend(hdr + body)
        return lnum

    def write_data(lnum: int, fields: list[tuple], values: list):
        buf.extend(struct.pack("B", lnum & 0x0F))
        for val, (_, size, _) in zip(values, fields):
            v = int(val) if val is not None else 0xFFFFFFFF
            if size == 4:
                buf.extend(struct.pack("<I", v & 0xFFFFFFFF))
            elif size == 2:
                buf.extend(struct.pack("<H", v & 0xFFFF))
            else:
                buf.extend(struct.pack("B", v & 0xFF))

    start_ts  = to_fit_ts(workout["start_time"])
    end_ts    = to_fit_ts(workout["end_time"])
    elapsed_s = end_ts - start_ts
    elapsed_ms = elapsed_s * 1000

    # ── file_id (mesg 0) ──────────────────────────────────────────────────────
    # Fields: type(enum/1B), manufacturer(uint16/2B), product(uint16/2B), time_created(uint32/4B)
    FILE_ID_FIELDS = [(0, 1, 0x00), (1, 2, 0x84), (2, 2, 0x84), (4, 4, 0x86)]
    ln_file_id = define(0, FILE_ID_FIELDS)
    write_data(ln_file_id, FILE_ID_FIELDS, [4, 255, 0, start_ts])

    # ── record messages (mesg 20) — HR data ───────────────────────────────────
    if hr_data:
        RECORD_FIELDS = [(253, 4, 0x86), (3, 1, 0x02)]  # timestamp, heart_rate
        ln_record = define(20, RECORD_FIELDS)
        for recorded_at, hr in hr_data:
            if hr is None:
                continue
            write_data(ln_record, RECORD_FIELDS, [to_fit_ts(recorded_at), int(hr)])

    # ── lap messages (mesg 19) — one per exercise ─────────────────────────────
    LAP_FIELDS = [
        (253, 4, 0x86),  # timestamp
        (2,   4, 0x86),  # start_time
        (7,   4, 0x86),  # total_elapsed_time (ms)
        (0,   1, 0x00),  # event (9=lap)
        (1,   1, 0x00),  # event_type (1=stop)
        (25,  1, 0x00),  # sport (14=training)
    ]
    ln_lap = define(19, LAP_FIELDS)
    num_ex = max(len(exercises), 1)
    lap_dur = elapsed_s // num_ex
    for i, _ in enumerate(exercises):
        lap_start = start_ts + i * lap_dur
        lap_end   = lap_start + lap_dur
        write_data(ln_lap, LAP_FIELDS, [lap_end, lap_start, lap_dur * 1000, 9, 1, 14])

    # ── session message (mesg 18) ─────────────────────────────────────────────
    SESSION_FIELDS = [
        (253, 4, 0x86),  # timestamp
        (2,   4, 0x86),  # start_time
        (7,   4, 0x86),  # total_elapsed_time (ms)
        (8,   4, 0x86),  # total_timer_time (ms)
        (0,   1, 0x00),  # event (9=session)
        (1,   1, 0x00),  # event_type (1=stop)
        (5,   1, 0x00),  # sport (14=training)
        (6,   1, 0x00),  # sub_sport (20=strength_training)
        (9,   2, 0x84),  # total_cycles (use as num laps)
    ]
    ln_session = define(18, SESSION_FIELDS)
    write_data(ln_session, SESSION_FIELDS, [
        end_ts, start_ts, elapsed_ms, elapsed_ms,
        9, 1, 14, 20, len(exercises)
    ])

    # ── activity message (mesg 34) ────────────────────────────────────────────
    ACTIVITY_FIELDS = [
        (253, 4, 0x86),  # timestamp
        (1,   4, 0x86),  # total_timer_time (ms)
        (2,   2, 0x84),  # num_sessions
        (0,   1, 0x00),  # event (26=activity)
        (1,   1, 0x00),  # event_type (1=stop)
        (3,   1, 0x00),  # type (0=manual)
    ]
    ln_activity = define(34, ACTIVITY_FIELDS)
    write_data(ln_activity, ACTIVITY_FIELDS, [end_ts, elapsed_ms, 1, 26, 1, 0])

    data_bytes = bytes(buf)

    # ── file header ───────────────────────────────────────────────────────────
    header = struct.pack(
        "<BBHI4s",
        14,                    # header_size
        0x10,                  # protocol_version
        2132,                  # profile_version
        len(data_bytes),       # data_size
        b".FIT"
    )
    header_crc = fit_crc(header)
    header += struct.pack("<H", header_crc)

    file_bytes = header + data_bytes
    data_crc = fit_crc(data_bytes)
    return file_bytes + struct.pack("<H", data_crc)


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
    print(f"HR data points: {len(hr_data)}")

    print("Building FIT file...")
    fit_bytes = build_fit(workout, exercises, hr_data)
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
