import os
import re
import json
import psycopg2
import psycopg2.extras
import requests
from collections import defaultdict
from psycopg2 import sql
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server.auth.providers.workos import AuthKitProvider

# -------------------------------------------------------------------
# Environment / Auth
# -------------------------------------------------------------------

load_dotenv()

WORKOS_AUTHKIT_DOMAIN = os.environ.get("WORKOS_AUTHKIT_DOMAIN")
BASE_URL = os.environ.get("BASE_URL")

if WORKOS_AUTHKIT_DOMAIN and BASE_URL:
    auth = AuthKitProvider(
        authkit_domain=WORKOS_AUTHKIT_DOMAIN,
        base_url=BASE_URL
    )
    mcp = FastMCP("Fitness Coach DB", auth=auth)
else:
    auth = None
    mcp = FastMCP("Fitness Coach DB")

DB_HOST = os.environ["DB_HOST"]
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "postgres")
DB_USER = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]

# write-enabled connection (for proposal workflow only)
DB_WRITE_HOST = os.environ.get("DB_WRITE_HOST", DB_HOST)
DB_WRITE_PORT = os.environ.get("DB_WRITE_PORT", DB_PORT)
DB_WRITE_NAME = os.environ.get("DB_WRITE_NAME", DB_NAME)
DB_WRITE_USER = os.environ.get("DB_WRITE_USER")
DB_WRITE_PASSWORD = os.environ.get("DB_WRITE_PASSWORD")

# Hevy API config (for push-back into the Hevy app)
HEVY_API_KEY = os.environ.get("HEVY_API_KEY")
HEVY_API_BASE = os.environ.get("HEVY_API_BASE", "https://api.hevyapp.com").rstrip("/")

# -------------------------------------------------------------------
# Safety / SQL guards
# -------------------------------------------------------------------

IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DISALLOWED_SQL_PATTERNS = [
    r"\bINSERT\b",
    r"\bUPDATE\b",
    r"\bDELETE\b",
    r"\bDROP\b",
    r"\bALTER\b",
    r"\bCREATE\b",
    r"\bTRUNCATE\b",
    r"\bGRANT\b",
    r"\bREVOKE\b",
    r"\bCOPY\b",
    r"\bCALL\b",
    r"\bVACUUM\b",
    r"\bANALYZE\b",
    r"\bCOMMENT\b",
    r"\bREFRESH\b",
    r"\bMERGE\b",
    r"\bDO\b",
    r"\bSET\b",
    r"\bRESET\b",
]


def validate_identifier(name: str) -> str:
    if not IDENTIFIER_RE.match(name):
        raise ValueError(f"Invalid identifier: {name}")
    return name


def clamp_limit(value: int, min_value: int, max_value: int) -> int:
    if value < min_value:
        return min_value
    if value > max_value:
        return max_value
    return value


def get_conn():
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )
    conn.set_session(readonly=True, autocommit=True)
    return conn


def get_write_conn():
    if not DB_WRITE_USER or not DB_WRITE_PASSWORD:
        raise RuntimeError("DB_WRITE_USER / DB_WRITE_PASSWORD are not configured.")

    conn = psycopg2.connect(
        host=DB_WRITE_HOST,
        port=DB_WRITE_PORT,
        dbname=DB_WRITE_NAME,
        user=DB_WRITE_USER,
        password=DB_WRITE_PASSWORD,
    )
    conn.autocommit = False
    return conn


def run_query(sql_text: str, params: tuple = ()):
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql_text, params)
            rows = cur.fetchall()
            return [dict(r) for r in rows]
    finally:
        conn.close()


def run_query_one(sql_text: str, params: tuple = ()):
    rows = run_query(sql_text, params)
    return rows[0] if rows else {}


def run_query_composed(query, params: tuple = ()):
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
            return [dict(r) for r in rows]
    finally:
        conn.close()


def run_scalar(sql_text: str, params: tuple = ()):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql_text, params)
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def run_write(sql_text: str, params: tuple = ()):
    conn = get_write_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql_text, params)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def run_write_returning_one(sql_text: str, params: tuple = ()):
    conn = get_write_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql_text, params)
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else {}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_readonly_sql(sql_text: str) -> str:
    text = sql_text.strip()

    if not text:
        raise ValueError("SQL cannot be empty.")

    if ";" in text:
        raise ValueError("Only single-statement read-only SQL is allowed.")

    upper = text.upper().lstrip()
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        raise ValueError("Only SELECT or WITH queries are allowed.")

    for pattern in DISALLOWED_SQL_PATTERNS:
        if re.search(pattern, upper, flags=re.IGNORECASE):
            raise ValueError(f"Disallowed SQL detected: {pattern}")

    return text


def relation_exists(relation_name: str, schema_name: str = "public") -> bool:
    validate_identifier(relation_name)
    validate_identifier(schema_name)
    reg = run_scalar("select to_regclass(%s)", (f"{schema_name}.{relation_name}",))
    return reg is not None


def dataset_exists(dataset_name: str, schema_name: str = "public") -> bool:
    validate_identifier(dataset_name)
    validate_identifier(schema_name)

    sql_text = """
    select exists (
        select 1
        from information_schema.tables
        where table_schema = %s
          and table_name = %s
    )
    """
    return bool(run_scalar(sql_text, (schema_name, dataset_name)))


def view_exists(view_name: str, schema_name: str = "public") -> bool:
    validate_identifier(view_name)
    validate_identifier(schema_name)

    sql_text = """
    select exists (
        select 1
        from information_schema.views
        where table_schema = %s
          and table_name = %s
    )
    """
    return bool(run_scalar(sql_text, (schema_name, view_name)))


def get_dataset_columns(dataset_name: str, schema_name: str = "public") -> set:
    validate_identifier(dataset_name)
    validate_identifier(schema_name)

    sql_text = """
    select column_name
    from information_schema.columns
    where table_schema = %s
      and table_name = %s
    """
    rows = run_query(sql_text, (schema_name, dataset_name))
    return {row["column_name"] for row in rows}


# -------------------------------------------------------------------
# Hevy API helpers (push approved changes back into the Hevy app)
# -------------------------------------------------------------------

def hevy_headers():
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

    Assumes:
      - hevy_routines
      - hevy_routine_exercises
      - hevy_routine_sets

    Returns:
      {
        "routine_id": "<hevy routine id>",
        "payload": { "routine": { ... } }
      }

    NOTE:
    Hevy's API may require a slightly different surface shape depending on their current schema.
    This wrapper uses the common top-level "routine" envelope.
    """
    if not dataset_exists("hevy_routines") or not dataset_exists("hevy_routine_exercises") or not dataset_exists("hevy_routine_sets"):
        raise RuntimeError("hevy routine tables do not all exist.")

    routine_rows = run_query(
        """
        select routine_id, title, folder_id, raw_json
        from hevy_routines
        where title = %s
        limit 1
        """,
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

    rows = run_query(
        """
        select
          e.exercise_index,
          e.title as exercise_name,
          e.notes as exercise_notes,
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
        """,
        (routine_id,)
    )

    grouped_sets = defaultdict(list)
    exercise_meta = {}

    for row in rows:
        ex_idx = row["exercise_index"]

        exercise_meta[ex_idx] = {
            "title": row["exercise_name"],
            "notes": row["exercise_notes"],
            "exercise_template_id": row["exercise_template_id"],
            "superset_id": row["superset_id"],
            "rest_seconds": row["rest_seconds"],
        }

        set_obj = {
            "type": row["set_type"],
        }
        if row["weight_kg"] is not None:
            set_obj["weight_kg"] = float(row["weight_kg"])
        if row["reps"] is not None:
            set_obj["reps"] = int(row["reps"])
        if row["distance_meters"] is not None:
            set_obj["distance_meters"] = float(row["distance_meters"])
        if row["duration_seconds"] is not None:
            set_obj["duration_seconds"] = int(row["duration_seconds"])
        if row["custom_metric"] is not None:
            set_obj["custom_metric"] = float(row["custom_metric"])

        grouped_sets[ex_idx].append(set_obj)

    exercises = []
    for ex_idx in sorted(grouped_sets.keys()):
        meta = exercise_meta[ex_idx]
        exercise_obj = {
            "sets": grouped_sets[ex_idx]
        }

        if meta.get("exercise_template_id"):
            exercise_obj["exercise_template_id"] = meta["exercise_template_id"]

        # keep title for readability / local consistency
        if meta.get("title"):
            exercise_obj["title"] = meta["title"]

        if meta.get("notes"):
            exercise_obj["notes"] = meta["notes"]
        if meta.get("superset_id"):
            exercise_obj["superset_id"] = meta["superset_id"]
        if meta.get("rest_seconds") is not None:
            exercise_obj["rest_seconds"] = int(meta["rest_seconds"])

        exercises.append(exercise_obj)

    routine_body = {
        "title": routine["title"],
        "folder_id": routine.get("folder_id"),
        "notes": routine_raw.get("notes"),
        "exercises": exercises,
    }

    return {
        "routine_id": routine_id,
        "payload": {
            "routine": routine_body
        }
    }


def push_routine_to_hevy_internal(routine_title: str) -> dict:
    """
    Push the current DB-backed routine definition to Hevy.

    This uses:
      PUT /v1/routines/{routineId}
    """
    built = build_hevy_routine_payload_from_db(routine_title)
    routine_id = built["routine_id"]
    payload = built["payload"]

    url = f"{HEVY_API_BASE}/v1/routines/{routine_id}"
    resp = requests.put(url, headers=hevy_headers(), json=payload, timeout=30)

    if resp.status_code >= 400:
        raise RuntimeError(f"Hevy routine update failed ({resp.status_code}): {resp.text}")

    try:
        response_json = resp.json()
    except Exception:
        response_json = {"raw_text": resp.text}

    return {
        "routine_id": routine_id,
        "routine_title": routine_title,
        "hevy_response": response_json,
        "payload_sent": payload,
    }


# -------------------------------------------------------------------
# Generic schema exploration tools
# -------------------------------------------------------------------

@mcp.tool()
def list_available_datasets() -> list:
    """
    List all readable tables and views in non-system schemas.
    """
    sql_text = """
    select
        table_schema,
        table_name,
        table_type
    from information_schema.tables
    where table_schema not in ('pg_catalog', 'information_schema')
    order by table_schema, table_name
    """
    return run_query(sql_text)


@mcp.tool()
def describe_dataset(dataset_name: str, schema_name: str = "public") -> list:
    """
    Describe columns, types, nullability, and defaults for a table/view.
    """
    validate_identifier(dataset_name)
    validate_identifier(schema_name)

    sql_text = """
    select
        column_name,
        data_type,
        is_nullable,
        column_default,
        ordinal_position
    from information_schema.columns
    where table_schema = %s
      and table_name = %s
    order by ordinal_position
    """
    return run_query(sql_text, (schema_name, dataset_name))


@mcp.tool()
def preview_dataset(dataset_name: str, schema_name: str = "public", limit: int = 25) -> list:
    """
    Return sample rows from any readable table/view.
    """
    validate_identifier(dataset_name)
    validate_identifier(schema_name)
    limit = clamp_limit(limit, 1, 200)

    query = sql.SQL("select * from {}.{} limit {}").format(
        sql.Identifier(schema_name),
        sql.Identifier(dataset_name),
        sql.Literal(limit),
    )
    return run_query_composed(query)


@mcp.tool()
def query_readonly(sql_text: str, limit: int = 200) -> list:
    """
    Run a read-only SELECT/WITH query against the database.
    The result set is wrapped and capped to a maximum row count.
    """
    safe_sql = ensure_readonly_sql(sql_text)
    limit = clamp_limit(limit, 1, 500)

    wrapped_sql = f"select * from ({safe_sql}) as q limit %s"
    return run_query(wrapped_sql, (limit,))


@mcp.tool()
def search_columns(column_search: str) -> list:
    """
    Search for tables/views containing column names matching a pattern.
    """
    sql_text = """
    select
        table_schema,
        table_name,
        column_name,
        data_type
    from information_schema.columns
    where table_schema not in ('pg_catalog', 'information_schema')
      and column_name ilike %s
    order by table_schema, table_name, ordinal_position
    """
    return run_query(sql_text, (f"%{column_search}%",))


# -------------------------------------------------------------------
# Existing curated tools
# -------------------------------------------------------------------

@mcp.tool()
def get_last_7_days_summary() -> list:
    """
    Return the last 7 days of joined recovery + nutrition data.
    """
    if not relation_exists("nutrition_recovery_daily"):
        return [{"message": "nutrition_recovery_daily view does not exist yet."}]

    sql_text = """
    select *
    from nutrition_recovery_daily
    order by date desc
    limit 7
    """
    return run_query(sql_text)


@mcp.tool()
def get_latest_day_context() -> dict:
    """
    Return the latest recovery + nutrition row plus the latest activity row.
    """
    latest_daily = None
    latest_activity = None

    if relation_exists("nutrition_recovery_daily"):
        rows = run_query("""
            select *
            from nutrition_recovery_daily
            order by date desc
            limit 1
        """)
        latest_daily = rows[0] if rows else None

    if relation_exists("activity_recovery_daily"):
        rows = run_query("""
            select *
            from activity_recovery_daily
            order by activity_date desc
            limit 1
        """)
        latest_activity = rows[0] if rows else None

    return {
        "latest_daily": latest_daily,
        "latest_activity": latest_activity,
    }


@mcp.tool()
def get_current_hevy_routines(routine_title: str = "") -> list:
    """
    Return Hevy routine context. Optional filter by routine_title.
    """
    if not relation_exists("hevy_routine_context"):
        return [{"message": "hevy_routine_context view does not exist yet."}]

    if routine_title:
        sql_text = """
        select *
        from hevy_routine_context
        where routine_title = %s
        order by exercise_index, set_index
        """
        return run_query(sql_text, (routine_title,))
    else:
        sql_text = """
        select *
        from hevy_routine_context
        order by routine_title, exercise_index, set_index
        """
        return run_query(sql_text)


# -------------------------------------------------------------------
# Strava tools
# -------------------------------------------------------------------

@mcp.tool()
def get_recent_strava_activities(limit: int = 30, sport_type: str = "") -> list:
    """
    Return recent Strava activities, optionally filtered by sport_type.
    """
    if not dataset_exists("strava_activities"):
        return [{"message": "strava_activities table does not exist yet."}]

    limit = clamp_limit(limit, 1, 200)

    if sport_type:
        sql_text = """
        select *
        from strava_activities
        where sport_type = %s
        order by activity_date desc
        limit %s
        """
        return run_query(sql_text, (sport_type, limit))
    else:
        sql_text = """
        select *
        from strava_activities
        order by activity_date desc
        limit %s
        """
        return run_query(sql_text, (limit,))


@mcp.tool()
def get_strava_activity_detail(strava_activity_id: int) -> dict:
    """
    Return one Strava activity by ID.
    """
    if not dataset_exists("strava_activities"):
        return {"message": "strava_activities table does not exist yet."}

    sql_text = """
    select *
    from strava_activities
    where strava_activity_id = %s
    limit 1
    """
    return run_query_one(sql_text, (strava_activity_id,))


@mcp.tool()
def get_recent_activity_context(limit: int = 20) -> list:
    """
    Return recent activities joined with Garmin + nutrition context.
    """
    if not relation_exists("activity_recovery_daily"):
        return [{"message": "activity_recovery_daily view does not exist yet."}]

    limit = clamp_limit(limit, 1, 100)

    sql_text = """
    select *
    from activity_recovery_daily
    order by activity_date desc
    limit %s
    """
    return run_query(sql_text, (limit,))


@mcp.tool()
def get_recent_ride_power_summary(limit: int = 20) -> list:
    """
    Return recent rides with power-related summary metrics.
    """
    if not dataset_exists("strava_activities"):
        return [{"message": "strava_activities table does not exist yet."}]

    limit = clamp_limit(limit, 1, 100)

    sql_text = """
    select
        strava_activity_id,
        activity_date,
        name,
        sport_type,
        distance_m,
        moving_time_s,
        elapsed_time_s,
        average_speed,
        average_heartrate,
        max_heartrate,
        average_watts,
        weighted_average_watts,
        max_watts,
        kilojoules
    from strava_activities
    where sport_type = 'Ride'
    order by activity_date desc
    limit %s
    """
    return run_query(sql_text, (limit,))


# -------------------------------------------------------------------
# Nutrition tools
# -------------------------------------------------------------------

@mcp.tool()
def get_nutrition_history(limit: int = 30) -> list:
    """
    Return recent nutrition history.
    """
    if not dataset_exists("daily_nutrition"):
        return [{"message": "daily_nutrition table does not exist yet."}]

    limit = clamp_limit(limit, 1, 90)

    sql_text = """
    select *
    from daily_nutrition
    order by date desc
    limit %s
    """
    return run_query(sql_text, (limit,))


@mcp.tool()
def get_nutrition_for_date(date_text: str) -> dict:
    """
    Return nutrition for a specific date (YYYY-MM-DD).
    """
    if not dataset_exists("daily_nutrition"):
        return {"message": "daily_nutrition table does not exist yet."}

    sql_text = """
    select *
    from daily_nutrition
    where date = %s
    limit 1
    """
    return run_query_one(sql_text, (date_text,))


@mcp.tool()
def get_recent_nutrition_recovery(limit: int = 14) -> list:
    """
    Return joined nutrition + recovery rows.
    """
    if not relation_exists("nutrition_recovery_daily"):
        return [{"message": "nutrition_recovery_daily view does not exist yet."}]

    limit = clamp_limit(limit, 1, 60)

    sql_text = """
    select *
    from nutrition_recovery_daily
    order by date desc
    limit %s
    """
    return run_query(sql_text, (limit,))


# -------------------------------------------------------------------
# Garmin tools
# -------------------------------------------------------------------

@mcp.tool()
def get_recent_garmin_daily(limit: int = 30) -> list:
    """
    Return recent Garmin daily rows.
    """
    if not dataset_exists("garmin_daily"):
        return [{"message": "garmin_daily table does not exist yet."}]

    limit = clamp_limit(limit, 1, 90)

    sql_text = """
    select *
    from garmin_daily
    order by date desc
    limit %s
    """
    return run_query(sql_text, (limit,))


@mcp.tool()
def get_sleep_trend(days: int = 14) -> list:
    """
    Return sleep score and total sleep duration over time.
    """
    if not dataset_exists("garmin_daily"):
        return [{"message": "garmin_daily table does not exist yet."}]

    days = clamp_limit(days, 1, 180)

    sql_text = """
    select
        date,
        sleep_score,
        sleep_seconds,
        awake_seconds
    from garmin_daily
    order by date desc
    limit %s
    """
    return run_query(sql_text, (days,))


@mcp.tool()
def get_sleep_stage_trend(days: int = 14) -> list:
    """
    Return deep/light/REM/awake sleep stage trend over time.
    """
    if not dataset_exists("garmin_daily"):
        return [{"message": "garmin_daily table does not exist yet."}]

    days = clamp_limit(days, 1, 180)

    sql_text = """
    select
        date,
        deep_sleep_seconds,
        light_sleep_seconds,
        rem_sleep_seconds,
        awake_seconds,
        sleep_seconds,
        sleep_score
    from garmin_daily
    order by date desc
    limit %s
    """
    return run_query(sql_text, (days,))


@mcp.tool()
def get_hrv_trend(days: int = 14) -> list:
    """
    Return HRV trend over time.
    """
    if not dataset_exists("garmin_daily"):
        return [{"message": "garmin_daily table does not exist yet."}]

    cols = get_dataset_columns("garmin_daily")
    if "hrv" not in cols:
        return [{"message": "hrv column does not exist in garmin_daily yet."}]

    days = clamp_limit(days, 1, 180)

    sql_text = """
    select
        date,
        hrv,
        resting_hr,
        stress_avg,
        training_readiness
    from garmin_daily
    where hrv is not null
    order by date desc
    limit %s
    """
    return run_query(sql_text, (days,))


@mcp.tool()
def get_vo2max_history(days: int = 60) -> list:
    """
    Return VO2 max history from garmin_daily.
    """
    if not dataset_exists("garmin_daily"):
        return [{"message": "garmin_daily table does not exist yet."}]

    cols = get_dataset_columns("garmin_daily")
    if "vo2_max" not in cols:
        return [{"message": "vo2_max column does not exist in garmin_daily yet."}]

    days = clamp_limit(days, 1, 365)

    sql_text = """
    select
        date,
        vo2_max,
        endurance_score,
        heat_acclimation
    from garmin_daily
    where vo2_max is not null
    order by date desc
    limit %s
    """
    return run_query(sql_text, (days,))


@mcp.tool()
def get_training_load_history(days: int = 30) -> list:
    """
    Return Garmin load / fatigue related fields over time.
    """
    if not dataset_exists("garmin_daily"):
        return [{"message": "garmin_daily table does not exist yet."}]

    days = clamp_limit(days, 1, 365)

    cols = get_dataset_columns("garmin_daily")
    wanted = [
        "date",
        "training_load",
        "acute_training_load",
        "chronic_training_load",
        "training_status",
        "training_readiness",
        "intensity_minutes_moderate",
        "intensity_minutes_vigorous",
        "stress_avg",
        "acwr_ratio",
        "acwr_percent",
        "training_balance_feedback",
    ]
    available = [c for c in wanted if c in cols]

    if len(available) <= 1:
        return [{"message": "No training load fields are available in garmin_daily yet."}]

    select_cols_sql = sql.SQL(", ").join(sql.Identifier(c) for c in available)
    query = sql.SQL("""
        select {cols}
        from public.garmin_daily
        order by date desc
        limit {limit}
    """).format(
        cols=select_cols_sql,
        limit=sql.Literal(days)
    )

    return run_query_composed(query)


@mcp.tool()
def get_recovery_signals(days: int = 14) -> list:
    """
    Return recovery-focused Garmin signals:
    readiness, HRV, body battery, respiration, SpO2, stress, sleep stages, body composition.
    """
    if not dataset_exists("garmin_daily"):
        return [{"message": "garmin_daily table does not exist yet."}]

    days = clamp_limit(days, 1, 180)

    cols = get_dataset_columns("garmin_daily")
    wanted = [
        "date",
        "training_readiness",
        "body_battery",
        "hrv",
        "resting_hr",
        "respiration_avg",
        "spo2_avg",
        "spo2_min",
        "stress_avg",
        "stress_max",
        "sleep_score",
        "sleep_seconds",
        "deep_sleep_seconds",
        "light_sleep_seconds",
        "rem_sleep_seconds",
        "awake_seconds",
        "recovery_time_hours",
        "weight_kg",
        "body_fat_pct",
        "body_water",
        "muscle_mass",
        "bone_mass",
        "bmi",
    ]
    available = [c for c in wanted if c in cols]

    if len(available) <= 1:
        return [{"message": "No recovery signal fields are available in garmin_daily yet."}]

    select_cols_sql = sql.SQL(", ").join(sql.Identifier(c) for c in available)
    query = sql.SQL("""
        select {cols}
        from public.garmin_daily
        order by date desc
        limit {limit}
    """).format(
        cols=select_cols_sql,
        limit=sql.Literal(days)
    )

    return run_query_composed(query)


# -------------------------------------------------------------------
# Hevy tools
# -------------------------------------------------------------------

@mcp.tool()
def get_hevy_routine_names() -> list:
    """
    Return available Hevy routine names.
    """
    if not dataset_exists("hevy_routines"):
        return [{"message": "hevy_routines table does not exist yet."}]

    sql_text = """
    select
        routine_id,
        title,
        folder_id,
        created_at_source,
        updated_at_source
    from hevy_routines
    order by title
    """
    return run_query(sql_text)


@mcp.tool()
def get_hevy_routine_detail(routine_title: str) -> list:
    """
    Return one Hevy routine with exercises and sets.
    """
    if not relation_exists("hevy_routine_context"):
        return [{"message": "hevy_routine_context view does not exist yet."}]

    sql_text = """
    select *
    from hevy_routine_context
    where routine_title = %s
    order by exercise_index, set_index
    """
    return run_query(sql_text, (routine_title,))


@mcp.tool()
def get_recent_hevy_workouts(limit: int = 20) -> list:
    """
    Return recent completed Hevy workouts if the table exists.
    """
    if not dataset_exists("hevy_workouts"):
        return [{
            "message": "hevy_workouts table does not exist yet. Only routines may be synced."
        }]

    limit = clamp_limit(limit, 1, 100)

    sql_text = """
    select *
    from hevy_workouts
    order by start_time desc
    limit %s
    """
    return run_query(sql_text, (limit,))


@mcp.tool()
def get_hevy_workout_detail(workout_id: str) -> list:
    """
    Return one completed Hevy workout if workout detail view/table exists.
    """
    if relation_exists("hevy_workout_context"):
        sql_text = """
        select *
        from hevy_workout_context
        where workout_id = %s
        order by exercise_index, set_index
        """
        return run_query(sql_text, (workout_id,))

    if dataset_exists("hevy_workout_sets"):
        sql_text = """
        select *
        from hevy_workout_sets
        where workout_id = %s
        order by exercise_index, set_index
        """
        return run_query(sql_text, (workout_id,))

    return [{
        "message": "No completed Hevy workout detail tables/views exist yet."
    }]


# -------------------------------------------------------------------
# Phase 2 / analytics tools
# -------------------------------------------------------------------

@mcp.tool()
def get_recent_power_curve(limit: int = 10) -> list:
    """
    Return recent rides with best 5-minute / 20-minute power and summary power metrics.
    """
    if not relation_exists("strava_power_curve_simple"):
        return [{"message": "strava_power_curve_simple view does not exist yet."}]

    limit = clamp_limit(limit, 1, 100)

    sql_text = """
    select *
    from strava_power_curve_simple
    order by activity_date desc
    limit %s
    """
    return run_query(sql_text, (limit,))


@mcp.tool()
def get_activity_best_efforts(strava_activity_id: int) -> dict:
    """
    Return best-effort summary for a specific ride.
    """
    if not relation_exists("strava_activity_best_efforts"):
        return {"message": "strava_activity_best_efforts view does not exist yet."}

    sql_text = """
    select *
    from strava_activity_best_efforts
    where strava_activity_id = %s
    limit 1
    """
    return run_query_one(sql_text, (strava_activity_id,))


@mcp.tool()
def get_weekly_strength_volume(weeks: int = 8) -> list:
    """
    Return weekly lifting volume / set count / total volume / avg RPE.
    """
    if not relation_exists("hevy_weekly_volume"):
        return [{"message": "hevy_weekly_volume view does not exist yet."}]

    weeks = clamp_limit(weeks, 1, 52)

    sql_text = """
    select *
    from hevy_weekly_volume
    order by week_start desc
    limit %s
    """
    return run_query(sql_text, (weeks,))


@mcp.tool()
def get_strength_progression(exercise_name: str, limit: int = 10) -> list:
    """
    Return progression history for a specific exercise.
    Supports partial matching via ILIKE.
    """
    if not relation_exists("hevy_exercise_progression"):
        return [{"message": "hevy_exercise_progression view does not exist yet."}]

    limit = clamp_limit(limit, 1, 100)

    sql_text = """
    select *
    from hevy_exercise_progression
    where exercise_name ilike %s
    order by workout_date desc
    limit %s
    """
    return run_query(sql_text, (f"%{exercise_name}%", limit))


@mcp.tool()
def get_muscle_group_fatigue(weeks: int = 6, muscle_group: str = "") -> list:
    """
    Return weekly fatigue by muscle group.
    Optional filter by primary_muscle_group.
    """
    if not relation_exists("hevy_muscle_group_fatigue"):
        return [{"message": "hevy_muscle_group_fatigue view does not exist yet."}]

    weeks = clamp_limit(weeks, 1, 52)

    if muscle_group:
        sql_text = """
        select *
        from hevy_muscle_group_fatigue
        where primary_muscle_group = %s
        order by week_start desc
        limit %s
        """
        return run_query(sql_text, (muscle_group, weeks))
    else:
        sql_text = """
        select *
        from hevy_muscle_group_fatigue
        order by week_start desc, primary_muscle_group
        limit %s
        """
        return run_query(sql_text, (weeks * 20,))


@mcp.tool()
def get_daily_training_nutrition_context(days: int = 14) -> list:
    """
    Return combined Garmin + nutrition + Strava + Hevy daily context.
    """
    if not relation_exists("daily_training_nutrition_context"):
        return [{"message": "daily_training_nutrition_context view does not exist yet."}]

    days = clamp_limit(days, 1, 180)

    sql_text = """
    select *
    from daily_training_nutrition_context
    order by date desc
    limit %s
    """
    return run_query(sql_text, (days,))


@mcp.tool()
def get_underfueling_signals(days: int = 14) -> list:
    """
    Return combined daily context with underfueling heuristics.
    """
    if not relation_exists("daily_underfueling_signals"):
        return [{"message": "daily_underfueling_signals view does not exist yet."}]

    days = clamp_limit(days, 1, 180)

    sql_text = """
    select *
    from daily_underfueling_signals
    order by date desc
    limit %s
    """
    return run_query(sql_text, (days,))


# -------------------------------------------------------------------
# Program change proposal tools (approval-gated write workflow)
# -------------------------------------------------------------------

@mcp.tool()
def create_program_change_proposal(
    routine_title: str,
    exercise_name: str,
    set_index: int,
    recommendation_type: str,
    proposed_weight_kg: float = None,
    proposed_reps: int = None,
    proposed_set_type: str = "",
    rationale: str = "",
    current_weight_kg: float = None,
    current_reps: int = None,
    current_set_type: str = "",
    supporting_context_json: str = "{}"
) -> dict:
    """
    Create a pending program change proposal.
    This does NOT modify the routine yet.
    """
    if not dataset_exists("hevy_program_change_proposals"):
        return {"message": "hevy_program_change_proposals table does not exist yet."}

    try:
        supporting_context = psycopg2.extras.Json(json.loads(supporting_context_json or "{}"))
    except Exception as e:
        return {"message": f"Invalid supporting_context_json: {e}"}

    sql_text = """
    insert into hevy_program_change_proposals (
        created_by,
        status,
        routine_title,
        exercise_name,
        set_index,
        current_weight_kg,
        current_reps,
        current_set_type,
        proposed_weight_kg,
        proposed_reps,
        proposed_set_type,
        recommendation_type,
        rationale,
        supporting_context
    )
    values (
        'claude',
        'pending',
        %s, %s, %s,
        %s, %s, %s,
        %s, %s, %s,
        %s, %s, %s
    )
    returning *
    """
    return run_write_returning_one(
        sql_text,
        (
            routine_title,
            exercise_name,
            set_index,
            current_weight_kg,
            current_reps,
            current_set_type or None,
            proposed_weight_kg,
            proposed_reps,
            proposed_set_type or None,
            recommendation_type,
            rationale or None,
            supporting_context,
        )
    )


@mcp.tool()
def get_pending_program_change_proposals(limit: int = 50) -> list:
    """
    Return pending program change proposals.
    """
    if not dataset_exists("hevy_program_change_proposals"):
        return [{"message": "hevy_program_change_proposals table does not exist yet."}]

    limit = clamp_limit(limit, 1, 200)

    sql_text = """
    select *
    from hevy_program_change_proposals
    where status = 'pending'
    order by created_at desc
    limit %s
    """
    return run_query(sql_text, (limit,))


@mcp.tool()
def get_program_change_proposals(status: str = "", limit: int = 100) -> list:
    """
    Return program change proposals.
    Optional status filter: pending / approved / rejected / applied
    """
    if not dataset_exists("hevy_program_change_proposals"):
        return [{"message": "hevy_program_change_proposals table does not exist yet."}]

    limit = clamp_limit(limit, 1, 500)

    if status:
        sql_text = """
        select *
        from hevy_program_change_proposals
        where status = %s
        order by created_at desc
        limit %s
        """
        return run_query(sql_text, (status, limit))

    sql_text = """
    select *
    from hevy_program_change_proposals
    order by created_at desc
    limit %s
    """
    return run_query(sql_text, (limit,))


@mcp.tool()
def update_program_change_status(
    proposal_id: str,
    new_status: str,
    reviewed_by: str = "frank"
) -> dict:
    """
    Update a proposal status to approved or rejected.
    """
    if not dataset_exists("hevy_program_change_proposals"):
        return {"message": "hevy_program_change_proposals table does not exist yet."}

    if new_status not in ("approved", "rejected"):
        return {"message": "new_status must be 'approved' or 'rejected'."}

    sql_text = """
    update hevy_program_change_proposals
    set
        status = %s,
        reviewed_by = %s,
        reviewed_at = now()
    where proposal_id = %s
    returning *
    """
    return run_write_returning_one(sql_text, (new_status, reviewed_by, proposal_id))


@mcp.tool()
def apply_program_change_proposal(proposal_id: str) -> dict:
    """
    Apply one approved proposal to hevy_routine_sets and mark it as applied.
    This updates the LOCAL DB-backed routine tables, not the Hevy app directly.
    """
    if not dataset_exists("hevy_program_change_proposals"):
        return {"message": "hevy_program_change_proposals table does not exist yet."}

    if not dataset_exists("hevy_routines") or not dataset_exists("hevy_routine_exercises") or not dataset_exists("hevy_routine_sets"):
        return {"message": "hevy routine base tables do not all exist yet."}

    conn = get_write_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                select *
                from hevy_program_change_proposals
                where proposal_id = %s
                for update
                """,
                (proposal_id,)
            )
            proposal = cur.fetchone()

            if not proposal:
                conn.rollback()
                return {"message": "Proposal not found."}

            proposal = dict(proposal)

            if proposal["status"] != "approved":
                conn.rollback()
                return {
                    "message": f"Proposal status must be 'approved' before apply. Current status: {proposal['status']}"
                }

            cur.execute(
                """
                update hevy_routine_sets rs
                set
                    weight_kg = coalesce(%s, rs.weight_kg),
                    reps = coalesce(%s, rs.reps),
                    set_type = coalesce(nullif(%s, ''), rs.set_type)
                from hevy_routine_exercises re
                join hevy_routines r
                  on re.routine_id = r.routine_id
                where rs.routine_id = re.routine_id
                  and rs.exercise_index = re.exercise_index
                  and r.title = %s
                  and re.title = %s
                  and rs.set_index = %s
                returning
                  r.title as routine_title,
                  re.title as exercise_name,
                  rs.set_index,
                  rs.weight_kg,
                  rs.reps,
                  rs.set_type
                """,
                (
                    proposal["proposed_weight_kg"],
                    proposal["proposed_reps"],
                    proposal["proposed_set_type"] or "",
                    proposal["routine_title"],
                    proposal["exercise_name"],
                    proposal["set_index"],
                )
            )

            updated_rows = cur.fetchall()

            if not updated_rows:
                conn.rollback()
                return {
                    "message": "No matching routine set row found to update.",
                    "proposal": proposal
                }

            cur.execute(
                """
                update hevy_program_change_proposals
                set
                    status = 'applied',
                    applied_at = now()
                where proposal_id = %s
                returning proposal_id, status, applied_at
                """,
                (proposal_id,)
            )
            applied_meta = dict(cur.fetchone())

        conn.commit()

        return {
            "message": "Proposal applied successfully.",
            "applied_proposal": applied_meta,
            "updated_rows": [dict(r) for r in updated_rows]
        }

    except Exception as e:
        conn.rollback()
        return {"message": f"Apply failed: {e}"}
    finally:
        conn.close()


@mcp.tool()
def push_routine_to_hevy(routine_title: str) -> dict:
    """
    Push the current DB-backed routine state to Hevy.
    Useful if a previous Hevy push failed and you want to retry.
    """
    try:
        result = push_routine_to_hevy_internal(routine_title)
        return {
            "message": "Routine pushed to Hevy successfully.",
            **result
        }
    except Exception as e:
        return {"message": f"Routine push failed: {e}"}


@mcp.tool()
def approve_and_apply_program_change_proposal(
    proposal_id: str,
    reviewed_by: str = "frank"
) -> dict:
    """
    Approve one proposal, apply it to the local DB routine tables,
    then push the updated routine to Hevy.
    """
    if not dataset_exists("hevy_program_change_proposals"):
        return {"message": "hevy_program_change_proposals table does not exist yet."}

    approved = update_program_change_status(proposal_id, "approved", reviewed_by=reviewed_by)
    if approved.get("message") and "proposal_id" not in approved:
        return approved

    applied = apply_program_change_proposal(proposal_id)
    if applied.get("message") != "Proposal applied successfully.":
        return {
            "message": "Proposal approved but local DB apply failed.",
            "approval_result": approved,
            "apply_result": applied
        }

    updated_rows = applied.get("updated_rows", [])
    if not updated_rows:
        return {
            "message": "Proposal applied locally, but no updated rows were returned.",
            "approval_result": approved,
            "apply_result": applied
        }

    routine_title = updated_rows[0]["routine_title"]

    try:
        push_result = push_routine_to_hevy_internal(routine_title)

        run_write(
            """
            update hevy_program_change_proposals
            set
                hevy_push_status = 'success',
                hevy_pushed_at = now(),
                hevy_push_error = null
            where proposal_id = %s
            """,
            (proposal_id,)
        )

        return {
            "message": "Proposal approved, applied locally, and pushed to Hevy successfully.",
            "approval_result": approved,
            "apply_result": applied,
            "hevy_push_result": push_result
        }

    except Exception as e:
        run_write(
            """
            update hevy_program_change_proposals
            set
                hevy_push_status = 'failed',
                hevy_push_error = %s
            where proposal_id = %s
            """,
            (str(e), proposal_id)
        )

        return {
            "message": "Proposal approved and applied locally, but pushing to Hevy failed.",
            "approval_result": approved,
            "apply_result": applied,
            "hevy_push_error": str(e),
            "routine_title": routine_title
        }


@mcp.tool()
def approve_and_apply_program_change_proposals(
    proposal_ids_json: str,
    reviewed_by: str = "frank"
) -> dict:
    """
    Approve and apply multiple proposals, then push each affected routine to Hevy once.

    proposal_ids_json example:
      ["uuid-1", "uuid-2", "uuid-3"]
    """
    if not dataset_exists("hevy_program_change_proposals"):
        return {"message": "hevy_program_change_proposals table does not exist yet."}

    try:
        proposal_ids = json.loads(proposal_ids_json or "[]")
    except Exception as e:
        return {"message": f"Invalid proposal_ids_json: {e}"}

    if not isinstance(proposal_ids, list) or not proposal_ids:
        return {"message": "proposal_ids_json must be a non-empty JSON array of proposal IDs."}

    results = []
    affected_routines = set()

    for proposal_id in proposal_ids:
        approved = update_program_change_status(proposal_id, "approved", reviewed_by=reviewed_by)
        applied = apply_program_change_proposal(proposal_id)

        item = {
            "proposal_id": proposal_id,
            "approval_result": approved,
            "apply_result": applied
        }

        updated_rows = applied.get("updated_rows", [])
        if updated_rows:
            affected_routines.add(updated_rows[0]["routine_title"])

        results.append(item)

    push_results = []
    for routine_title in sorted(affected_routines):
        try:
            push_result = push_routine_to_hevy_internal(routine_title)
            push_results.append({
                "routine_title": routine_title,
                "status": "success",
                "result": push_result
            })

            run_write(
                """
                update hevy_program_change_proposals
                set
                    hevy_push_status = 'success',
                    hevy_pushed_at = now(),
                    hevy_push_error = null
                where proposal_id = any(%s)
                  and routine_title = %s
                """,
                (proposal_ids, routine_title)
            )

        except Exception as e:
            push_results.append({
                "routine_title": routine_title,
                "status": "failed",
                "error": str(e)
            })

            run_write(
                """
                update hevy_program_change_proposals
                set
                    hevy_push_status = 'failed',
                    hevy_push_error = %s
                where proposal_id = any(%s)
                  and routine_title = %s
                """,
                (str(e), proposal_ids, routine_title)
            )

    return {
        "message": "Batch approve/apply completed.",
        "proposal_results": results,
        "hevy_push_results": push_results
    }


# -------------------------------------------------------------------
# Startup
# -------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=8000
    )