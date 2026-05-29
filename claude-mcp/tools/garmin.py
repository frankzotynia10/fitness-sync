from psycopg2 import sql
from db import run_query, run_query_composed, dataset_exists, get_dataset_columns
from utils import clamp_limit


def register(mcp):

    @mcp.tool()
    def get_recent_garmin_daily(limit: int = 30) -> list:
        """Return recent Garmin daily rows."""
        if not dataset_exists("garmin_daily"):
            return [{"message": "garmin_daily table does not exist yet."}]
        return run_query("select * from garmin_daily order by date desc limit %s",
                         (clamp_limit(limit, 1, 90),))

    @mcp.tool()
    def get_sleep_trend(days: int = 14) -> list:
        """Return sleep score and total sleep duration over time."""
        if not dataset_exists("garmin_daily"):
            return [{"message": "garmin_daily table does not exist yet."}]
        return run_query("""
            select date, sleep_score, sleep_seconds, awake_seconds
            from garmin_daily order by date desc limit %s
        """, (clamp_limit(days, 1, 180),))

    @mcp.tool()
    def get_sleep_stage_trend(days: int = 14) -> list:
        """Return deep/light/REM/awake sleep stage trend over time."""
        if not dataset_exists("garmin_daily"):
            return [{"message": "garmin_daily table does not exist yet."}]
        return run_query("""
            select date, deep_sleep_seconds, light_sleep_seconds,
                   rem_sleep_seconds, awake_seconds, sleep_seconds, sleep_score
            from garmin_daily order by date desc limit %s
        """, (clamp_limit(days, 1, 180),))

    @mcp.tool()
    def get_hrv_trend(days: int = 14) -> list:
        """Return HRV trend over time."""
        if not dataset_exists("garmin_daily"):
            return [{"message": "garmin_daily table does not exist yet."}]
        if "hrv" not in get_dataset_columns("garmin_daily"):
            return [{"message": "hrv column does not exist in garmin_daily yet."}]
        return run_query("""
            select date, hrv, resting_hr, stress_avg, training_readiness
            from garmin_daily where hrv is not null
            order by date desc limit %s
        """, (clamp_limit(days, 1, 180),))

    @mcp.tool()
    def get_vo2max_history(days: int = 60) -> list:
        """Return VO2 max history from garmin_daily."""
        if not dataset_exists("garmin_daily"):
            return [{"message": "garmin_daily table does not exist yet."}]
        if "vo2_max" not in get_dataset_columns("garmin_daily"):
            return [{"message": "vo2_max column does not exist in garmin_daily yet."}]
        return run_query("""
            select date, vo2_max, endurance_score, heat_acclimation
            from garmin_daily where vo2_max is not null
            order by date desc limit %s
        """, (clamp_limit(days, 1, 365),))

    @mcp.tool()
    def get_training_load_history(days: int = 30) -> list:
        """Return Garmin load / fatigue related fields over time."""
        if not dataset_exists("garmin_daily"):
            return [{"message": "garmin_daily table does not exist yet."}]
        cols = get_dataset_columns("garmin_daily")
        wanted = [
            "date", "training_load", "acute_training_load", "chronic_training_load",
            "training_status", "training_readiness", "intensity_minutes_moderate",
            "intensity_minutes_vigorous", "stress_avg", "acwr_ratio", "acwr_percent",
            "training_balance_feedback",
        ]
        available = [c for c in wanted if c in cols]
        if len(available) <= 1:
            return [{"message": "No training load fields are available in garmin_daily yet.",
                     "checked_columns": wanted[1:]}]
        select_sql = sql.SQL(", ").join(sql.Identifier(c) for c in available)
        query = sql.SQL("select {cols} from public.garmin_daily order by date desc limit {limit}").format(
            cols=select_sql, limit=sql.Literal(clamp_limit(days, 1, 365))
        )
        return run_query_composed(query)

    @mcp.tool()
    def get_recovery_signals(days: int = 14) -> list:
        """Return recovery-focused Garmin signals: readiness, HRV, body battery,
        respiration, SpO2, stress, sleep stages, body composition."""
        if not dataset_exists("garmin_daily"):
            return [{"message": "garmin_daily table does not exist yet."}]
        cols = get_dataset_columns("garmin_daily")
        wanted = [
            "date", "training_readiness", "body_battery", "hrv", "resting_hr",
            "respiration_avg", "spo2_avg", "spo2_min", "stress_avg", "stress_max",
            "sleep_score", "sleep_seconds", "deep_sleep_seconds", "light_sleep_seconds",
            "rem_sleep_seconds", "awake_seconds", "recovery_time_hours",
            "weight_kg", "body_fat_pct", "body_water", "muscle_mass", "bone_mass", "bmi",
        ]
        available = [c for c in wanted if c in cols]
        if len(available) <= 1:
            return [{"message": "No recovery signal fields are available in garmin_daily yet."}]
        select_sql = sql.SQL(", ").join(sql.Identifier(c) for c in available)
        query = sql.SQL("select {cols} from public.garmin_daily order by date desc limit {limit}").format(
            cols=select_sql, limit=sql.Literal(clamp_limit(days, 1, 180))
        )
        return run_query_composed(query)

    @mcp.tool()
    def get_underfueling_signals(days: int = 14) -> list:
        """Return combined daily context with underfueling heuristics."""
        if not dataset_exists("daily_underfueling_signals"):
            return [{"message": "daily_underfueling_signals view does not exist yet."}]
        return run_query("select * from daily_underfueling_signals order by date desc limit %s",
                         (clamp_limit(days, 1, 180),))
