"""
Debug script to generate and dump a FIT file for inspection.
Run: docker exec strava-sync python debug_fit.py
"""
import sys
import os
import psycopg2
import json
from services.fit_encoder import FitEncoder, get_exercise_enums
import struct

DB_HOST = os.environ["DB_HOST"]
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "postgres")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ["DB_PASSWORD"]

# Use the most recent workout
WORKOUT_ID = "a63cafaf-338b-46c0-a775-7f82301205db"  # Legs1

conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)

with conn.cursor() as cur:
    cur.execute("SELECT title, start_time, end_time FROM hevy_workouts WHERE workout_id = %s", (WORKOUT_ID,))
    row = cur.fetchone()
    title, start_time, end_time = row
    print(f"Workout: {title}, {start_time} -> {end_time}")

    cur.execute("""
        SELECT hwe.title, hwe.exercise_index, hws.set_index, hws.weight_kg, hws.reps, hws.set_type
        FROM hevy_workout_exercises hwe
        JOIN hevy_workout_sets hws ON hws.workout_id = hwe.workout_id AND hws.exercise_index = hwe.exercise_index
        WHERE hwe.workout_id = %s AND hws.set_type IN ('normal', 'warmup')
        ORDER BY hwe.exercise_index, hws.set_index
    """, (WORKOUT_ID,))
    rows = cur.fetchall()

exercises = {}
for ex_name, ex_idx, set_idx, weight_kg, reps, set_type in rows:
    if ex_idx not in exercises:
        exercises[ex_idx] = {"name": ex_name, "sets": []}
    exercises[ex_idx]["sets"].append({"weight_kg": weight_kg, "reps": reps, "set_type": set_type})

workout_data = {
    "title": title,
    "start_time": start_time,
    "end_time": end_time,
    "exercises": [exercises[i] for i in sorted(exercises.keys())],
}

print("\nExercise mappings:")
for ex in workout_data["exercises"]:
    cat, name = get_exercise_enums(ex["name"])
    print(f"  {ex['name']} -> category={cat}, name={name}, sets={len(ex['sets'])}")

encoder = FitEncoder()
fit_bytes = encoder.encode(workout_data)

output_path = "/tmp/debug_workout.fit"
with open(output_path, "wb") as f:
    f.write(fit_bytes)

print(f"\nFIT file written to {output_path} ({len(fit_bytes)} bytes)")
print("Hex dump of first 64 bytes:")
print(" ".join(f"{b:02X}" for b in fit_bytes[:64]))
print("\nRun: python -c \"import struct; data=open('/tmp/debug_workout.fit','rb').read(); print(len(data), 'bytes')\"")

conn.close()
