import os
import sys
import json
import requests
import psycopg2
from dotenv import load_dotenv

load_dotenv()

HEVY_API_KEY = os.environ["HEVY_API_KEY"]

DB_HOST = os.environ["DB_HOST"]
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "postgres")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ["DB_PASSWORD"]

HEADERS = {
    "accept": "application/json",
    "api-key": HEVY_API_KEY
}


def fetch_routines(page=1, page_size=10):
    url = f"https://api.hevyapp.com/v1/routines?page={page}&pageSize={page_size}"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_workouts(page=1, page_size=10):
    url = f"https://api.hevyapp.com/v1/workouts?page={page}&pageSize={page_size}"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_workout_detail(workout_id):
    url = f"https://api.hevyapp.com/v1/workouts/{workout_id}"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_all_routines(page_size=10):
    page = 1
    all_routines = []

    while True:
        data = fetch_routines(page=page, page_size=page_size)
        routines = data.get("routines", [])
        page_count = data.get("page_count", page)

        all_routines.extend(routines)

        if page >= page_count:
            break

        page += 1

    return all_routines


def fetch_all_workouts(page_size=10):
    page = 1
    all_workouts = []

    while True:
        data = fetch_workouts(page=page, page_size=page_size)
        workouts = data.get("workouts", [])
        page_count = data.get("page_count", page)

        all_workouts.extend(workouts)

        if page >= page_count:
            break

        page += 1

    return all_workouts


def sync_routines(conn, routines):
    with conn:
        with conn.cursor() as cur:
            for routine in routines:
                routine_id = routine["id"]

                # Upsert routine
                cur.execute(
                    """
                    insert into hevy_routines (
                        routine_id,
                        title,
                        folder_id,
                        created_at_source,
                        updated_at_source,
                        raw_json,
                        updated_at
                    )
                    values (%s, %s, %s, %s, %s, %s::jsonb, now())
                    on conflict (routine_id) do update set
                        title = excluded.title,
                        folder_id = excluded.folder_id,
                        created_at_source = excluded.created_at_source,
                        updated_at_source = excluded.updated_at_source,
                        raw_json = excluded.raw_json,
                        updated_at = now();
                    """,
                    (
                        routine_id,
                        routine.get("title"),
                        routine.get("folder_id"),
                        routine.get("created_at"),
                        routine.get("updated_at"),
                        json.dumps(routine)
                    )
                )

                # Clear child rows so we can reinsert cleanly
                cur.execute("delete from hevy_routine_sets where routine_id = %s", (routine_id,))
                cur.execute("delete from hevy_routine_exercises where routine_id = %s", (routine_id,))

                # Insert exercises + sets
                for exercise in routine.get("exercises", []):
                    exercise_index = exercise.get("index")

                    cur.execute(
                        """
                        insert into hevy_routine_exercises (
                            routine_id,
                            exercise_index,
                            title,
                            notes,
                            exercise_template_id,
                            superset_id,
                            rest_seconds,
                            raw_json
                        )
                        values (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                        """,
                        (
                            routine_id,
                            exercise_index,
                            exercise.get("title"),
                            exercise.get("notes"),
                            exercise.get("exercise_template_id"),
                            exercise.get("superset_id"),
                            exercise.get("rest_seconds"),
                            json.dumps(exercise)
                        )
                    )

                    for s in exercise.get("sets", []):
                        cur.execute(
                            """
                            insert into hevy_routine_sets (
                                routine_id,
                                exercise_index,
                                set_index,
                                set_type,
                                weight_kg,
                                reps,
                                distance_meters,
                                duration_seconds,
                                custom_metric,
                                raw_json
                            )
                            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                            """,
                            (
                                routine_id,
                                exercise_index,
                                s.get("index"),
                                s.get("type"),
                                s.get("weight_kg"),
                                s.get("reps"),
                                s.get("distance_meters"),
                                s.get("duration_seconds"),
                                s.get("custom_metric"),
                                json.dumps(s)
                            )
                        )


def sync_workouts(conn, workouts):
    with conn:
        with conn.cursor() as cur:
            for workout in workouts:
                workout_id = workout["id"]

                cur.execute(
                    """
                    insert into hevy_workouts (
                        workout_id,
                        title,
                        description,
                        start_time,
                        end_time,
                        created_at_source,
                        updated_at_source,
                        raw_json,
                        updated_at
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, now())
                    on conflict (workout_id) do update set
                        title = excluded.title,
                        description = excluded.description,
                        start_time = excluded.start_time,
                        end_time = excluded.end_time,
                        created_at_source = excluded.created_at_source,
                        updated_at_source = excluded.updated_at_source,
                        raw_json = excluded.raw_json,
                        updated_at = now();
                    """,
                    (
                        workout_id,
                        workout.get("title"),
                        workout.get("description") or workout.get("notes"),
                        workout.get("start_time"),
                        workout.get("end_time"),
                        workout.get("created_at"),
                        workout.get("updated_at"),
                        json.dumps(workout)
                    )
                )

                # Clear child rows and rebuild cleanly
                cur.execute("delete from hevy_workout_sets where workout_id = %s", (workout_id,))
                cur.execute("delete from hevy_workout_exercises where workout_id = %s", (workout_id,))

                for exercise in workout.get("exercises", []):
                    exercise_index = exercise.get("index")

                    cur.execute(
                        """
                        insert into hevy_workout_exercises (
                            workout_id,
                            exercise_index,
                            title,
                            notes,
                            exercise_template_id,
                            superset_id,
                            rest_seconds,
                            raw_json
                        )
                        values (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                        """,
                        (
                            workout_id,
                            exercise_index,
                            exercise.get("title"),
                            exercise.get("notes"),
                            exercise.get("exercise_template_id"),
                            exercise.get("superset_id"),
                            exercise.get("rest_seconds"),
                            json.dumps(exercise)
                        )
                    )

                    for s in exercise.get("sets", []):
                        cur.execute(
                            """
                            insert into hevy_workout_sets (
                                workout_id,
                                exercise_index,
                                set_index,
                                set_type,
                                weight_kg,
                                reps,
                                rpe,
                                distance_meters,
                                duration_seconds,
                                custom_metric,
                                raw_json
                            )
                            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                            """,
                            (
                                workout_id,
                                exercise_index,
                                s.get("index"),
                                s.get("type"),
                                s.get("weight_kg"),
                                s.get("reps"),
                                s.get("rpe"),
                                s.get("distance_meters"),
                                s.get("duration_seconds"),
                                s.get("custom_metric"),
                                json.dumps(s)
                            )
                        )


def main():
    try:
        print("Fetching Hevy routines...")
        routines = fetch_all_routines(page_size=10)
        print(f"Fetched {len(routines)} routines")

        print("Fetching Hevy workout summaries...")
        workout_summaries = fetch_all_workouts(page_size=10)
        print(f"Fetched {len(workout_summaries)} workout summaries")

        print("Fetching full workout details...")
        full_workouts = []
        for w in workout_summaries:
            wid = w["id"]
            print(f"Fetching workout detail for {wid}...")
            full_workouts.append(fetch_workout_detail(wid))

        print("Connecting to Postgres...")
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )

        sync_routines(conn, routines)
        sync_workouts(conn, full_workouts)

        conn.close()

        print("Hevy routine + workout sync complete.")

    except Exception as e:
        print(f"Hevy sync failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
    sys.exit(0)