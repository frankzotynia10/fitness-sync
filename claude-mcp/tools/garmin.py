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

    @mcp.tool()
    def get_weekly_coaching_context(weeks: int = 2) -> dict:
        """Return a single combined coaching context for weekly planning.
        Joins recovery signals, training load, ride volume, strength volume,
        nutrition, and sleep into one response. Replaces calling 5-6 separate
        tools for 'what should I do next week' conversations.

        Returns:
          - daily: last N weeks of day-by-day recovery + training + nutrition
          - weekly_strength: per-week lifting volume, set count, avg RPE
          - weekly_sleep: per-week sleep averages (score, duration, deep, REM)
          - recent_rides: last 10 rides with power and duration summary
          - acwr_summary: current acute vs chronic load and ACWR ratio
        """
        days = clamp_limit(weeks, 1, 12) * 7
        result = {}

        # 1. Daily context — recovery + training load + nutrition + ride data
        if dataset_exists("daily_training_nutrition_context"):
            result["daily"] = run_query("""
                select
                    date,
                    training_readiness,
                    hrv,
                    sleep_score,
                    round(deep_sleep_seconds::numeric / 60.0) as deep_sleep_min,
                    round(rem_sleep_seconds::numeric  / 60.0) as rem_sleep_min,
                    body_battery,
                    training_load,
                    acute_training_load,
                    chronic_training_load,
                    round(
                        case
                            when chronic_training_load > 0
                            then (acute_training_load / chronic_training_load)::numeric
                            else null
                        end, 2
                    ) as acwr,
                    training_status,
                    respiration_avg,
                    spo2_avg,
                    round(nutrition_calories::numeric)   as calories,
                    round(protein_g::numeric)            as protein_g,
                    round(carbs_g::numeric)              as carbs_g,
                    round(fat_g::numeric)                as fat_g,
                    round(ride_kj::numeric)              as ride_kj,
                    ride_count,
                    round(strength_volume_load::numeric) as strength_volume_load,
                    round(strength_avg_rpe::numeric, 1)  as strength_avg_rpe
                from daily_training_nutrition_context
                order by date desc
                limit %s
            """, (days,))
        else:
            result["daily"] = []

        # 2. Weekly strength volume
        if dataset_exists("hevy_weekly_volume"):
            result["weekly_strength"] = run_query("""
                select * from hevy_weekly_volume
                order by week_start desc
                limit %s
            """, (clamp_limit(weeks, 1, 12),))
        else:
            result["weekly_strength"] = []

        # 3. Weekly sleep summary — averages per week from garmin_daily directly
        if dataset_exists("garmin_daily"):
            result["weekly_sleep"] = run_query("""
                select
                    date_trunc('week', date)::date as week_start,
                    count(*) as days_with_data,
                    round(avg(sleep_score)::numeric, 1) as avg_sleep_score,
                    round(avg(sleep_seconds / 3600.0)::numeric, 2) as avg_sleep_hours,
                    round(avg(deep_sleep_seconds / 60.0)::numeric) as avg_deep_sleep_min,
                    round(avg(rem_sleep_seconds / 60.0)::numeric) as avg_rem_sleep_min,
                    round(avg(hrv)::numeric, 1) as avg_hrv,
                    round(avg(resting_hr)::numeric, 1) as avg_resting_hr
                from garmin_daily
                where date >= current_date - (%s * interval '1 week')
                  and sleep_score is not null
                group by date_trunc('week', date)
                order by week_start desc
            """, (clamp_limit(weeks, 1, 12),))
        else:
            result["weekly_sleep"] = []

        # 4. Recent rides — distance, duration, avg power, kJ
        if dataset_exists("strava_activities"):
            cols = get_dataset_columns("strava_activities")
            wanted = [
                "activity_date", "name", "sport_type",
                "distance_m", "moving_time_s", "elapsed_time_s",
                "average_watts", "max_watts", "kilojoules",
                "average_heartrate", "max_heartrate",
                "total_elevation_gain_m",
            ]
            available = [c for c in wanted if c in cols]
            select_sql = sql.SQL(", ").join(sql.Identifier(c) for c in available)
            query = sql.SQL("""
                select {cols}
                from public.strava_activities
                where sport_type in ('Ride', 'VirtualRide', 'MountainBikeRide')
                order by activity_date desc
                limit 10
            """).format(cols=select_sql)
            result["recent_rides"] = run_query_composed(query)
        else:
            result["recent_rides"] = []

        # 5. ACWR summary — latest values only for quick reference
        if dataset_exists("garmin_daily"):
            cols = get_dataset_columns("garmin_daily")
            acwr_cols = [c for c in [
                "date", "acute_training_load", "chronic_training_load",
                "acwr_ratio", "training_status", "training_readiness",
                "body_battery", "hrv", "recovery_time_hours",
            ] if c in cols]
            if len(acwr_cols) > 1:
                select_sql = sql.SQL(", ").join(sql.Identifier(c) for c in acwr_cols)
                query = sql.SQL("""
                    select {cols}
                    from public.garmin_daily
                    order by date desc
                    limit 1
                """).format(cols=select_sql)
                rows = run_query_composed(query)
                result["acwr_summary"] = rows[0] if rows else {}
            else:
                result["acwr_summary"] = {}
        else:
            result["acwr_summary"] = {}

        return result
