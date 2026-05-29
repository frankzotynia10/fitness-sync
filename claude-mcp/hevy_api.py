import json
import requests
from collections import defaultdict
from config import HEVY_API_KEY, HEVY_API_BASE
from db import run_query, dataset_exists


def hevy_headers() -> dict:
    if not HEVY_API_KEY:
        raise RuntimeError("HEVY_API_KEY is not configured.")
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "api-key": HEVY_API_KEY,
    }


def build_hevy_routine_payload_from_db(routine_title: str) -> dict:
    """
    Build a Hevy-compatible routine update payload from local DB tables.
    Returns { "routine_id": str, "payload": { "routine": {...} } }
    """
    if not all(dataset_exists(t) for t in ("hevy_routines", "hevy_routine_exercises", "hevy_routine_sets")):
        raise RuntimeError("hevy routine tables do not all exist.")

    routine_rows = run_query(
        "select routine_id, title, folder_id, raw_json from hevy_routines where title = %s limit 1",
        (routine_title,)
    )
    if not routine_rows:
        raise RuntimeError(f"Routine '{routine_title}' not found in hevy_routines.")

    routine = routine_rows[0]
    routine_id = routine["routine_id"]

    routine_raw = routine.get("raw_json") or {}
    if isinstance(routine_raw, str):
        try:
            routine_raw = json.loads(routine_raw)
        except Exception:
            routine_raw = {}

    rows = run_query("""
        select
            e.exercise_index,
            e.notes       as exercise_notes,
            e.exercise_template_id,
            e.superset_id,
            e.rest_seconds,
            s.set_index,
            s.set_type,
            s.weight_kg,
            s.reps,
            s.distance_meters,
            s.duration_seconds,
            s.custom_metric
        from hevy_routine_exercises e
        join hevy_routine_sets s
          on e.routine_id = s.routine_id
         and e.exercise_index = s.exercise_index
        where e.routine_id = %s
        order by e.exercise_index, s.set_index
    """, (routine_id,))

    grouped_sets = defaultdict(list)
    exercise_meta = {}

    for row in rows:
        ex_idx = row["exercise_index"]
        exercise_meta[ex_idx] = {
            "notes":                row["exercise_notes"],
            "exercise_template_id": row["exercise_template_id"],
            "superset_id":          row["superset_id"],
            "rest_seconds":         row["rest_seconds"],
        }
        set_obj = {"type": row["set_type"]}
        if row["weight_kg"]       is not None: set_obj["weight_kg"]       = float(row["weight_kg"])
        if row["reps"]            is not None: set_obj["reps"]            = int(row["reps"])
        if row["distance_meters"] is not None: set_obj["distance_meters"] = float(row["distance_meters"])
        if row["duration_seconds"] is not None: set_obj["duration_seconds"] = int(row["duration_seconds"])
        if row["custom_metric"]   is not None: set_obj["custom_metric"]   = float(row["custom_metric"])
        grouped_sets[ex_idx].append(set_obj)

    exercises = []
    for ex_idx in sorted(grouped_sets.keys()):
        meta = exercise_meta[ex_idx]
        ex = {"sets": grouped_sets[ex_idx]}
        if meta.get("exercise_template_id"): ex["exercise_template_id"] = meta["exercise_template_id"]
        if meta.get("notes"):                ex["notes"]                = meta["notes"]
        if meta.get("superset_id"):          ex["superset_id"]          = meta["superset_id"]
        if meta.get("rest_seconds") is not None: ex["rest_seconds"]     = int(meta["rest_seconds"])
        exercises.append(ex)

    return {
        "routine_id": routine_id,
        "payload": {
            "routine": {
                "title":     routine["title"],
                "notes":     routine_raw.get("notes"),
                "exercises": exercises,
            }
        }
    }


def push_routine_to_hevy_internal(routine_title: str) -> dict:
    """
    PUT /v1/routines/{routineId} with current DB-backed routine state.
    """
    built = build_hevy_routine_payload_from_db(routine_title)
    routine_id = built["routine_id"]
    payload    = built["payload"]

    url  = f"{HEVY_API_BASE}/v1/routines/{routine_id}"
    resp = requests.put(url, headers=hevy_headers(), json=payload, timeout=30)

    if resp.status_code >= 400:
        raise RuntimeError(f"Hevy routine update failed ({resp.status_code}): {resp.text}")

    try:
        response_json = resp.json()
    except Exception:
        response_json = {"raw_text": resp.text}

    return {
        "routine_id":    routine_id,
        "routine_title": routine_title,
        "hevy_response": response_json,
        "payload_sent":  payload,
    }
