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
        """Return sleep score, duration, and user notes over time."""
        if not dataset_exists("garmin_daily"):
            return [{"message": "garmin_daily table does not exist yet."}]
        return run_query("""
            select date, sleep_score, sleep_seconds, awake_seconds, sleep_notes
            from garmin_daily order by date desc limit %s
        """, (clamp_limit(days, 1, 180),))

    @mcp.tool()
    def get_sleep_stage_trend(days: int = 14) -> list:
        """Return deep/light/REM/awake sleep stage trend over time."""
        if not dataset_exists("garmin_daily"):
            return [{"message": "garmin_daily table does not exist yet."}]
        return run_query("""
            select date, deep_sleep_seconds, light_sleep_seconds,
                   rem_sleep_seconds, awake_seconds, sleep_seconds, sleep_score, sleep_notes
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
        respiration, SpO2, stress, sleep stages, sleep notes, body composition."""
        if not dataset_exists("garmin_daily"):
            return [{"message": "garmin_daily table does not exist yet."}]
        cols = get_dataset_columns("garmin_daily")
        wanted = [
            "date", "training_readiness", "body_battery", "hrv", "resting_hr",
            "respiration_avg", "spo2_avg", "spo2_min", "stress_avg", "stress_max",
            "sleep_score", "sleep_seconds", "sleep_notes", "deep_sleep_seconds",
            "light_sleep_seconds", "rem_sleep_seconds", "awake_seconds",
            "recovery_time_hours", "weight_kg", "body_fat_pct", "body_water",
            "muscle_mass", "bone_mass", "bmi",
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
    def get_body_composition_trend(days: int = 90) -> dict:
        """Return body composition trend over time: weight in lbs, body fat percentage,
        muscle mass, and BMI. Useful for tracking body recomposition progress.
        Returns trend data plus summary stats (start, current, change)."""
        if not dataset_exists("garmin_daily"):
            return {"message": "garmin_daily table does not exist yet."}

        rows = run_query("""
            SELECT
                date,
                ROUND((weight_kg * 2.20462)::numeric, 1) AS weight_lb,
                ROUND(weight_kg::numeric, 2) AS weight_kg,
                ROUND(body_fat_pct::numeric, 1) AS body_fat_pct,
                ROUND((muscle_mass * 2.20462)::numeric, 1) AS muscle_mass_lb,
                ROUND(bmi::numeric, 1) AS bmi
            FROM garmin_daily
            WHERE weight_kg IS NOT NULL
              AND date >= CURRENT_DATE - (%s * INTERVAL '1 day')
            ORDER BY date DESC
        """, (clamp_limit(days, 7, 365),))

        if not rows:
            return {"message": "No body composition data found."}

        current = rows[0]
        oldest = rows[-1]

        weight_change = None
        fat_change = None
        if current["weight_lb"] and oldest["weight_lb"]:
            weight_change = round(float(current["weight_lb"]) - float(oldest["weight_lb"]), 1)
        if current["body_fat_pct"] and oldest["body_fat_pct"]:
            fat_change = round(float(current["body_fat_pct"]) - float(oldest["body_fat_pct"]), 1)

        return {
            "current_weight_lb": current["weight_lb"],
            "current_body_fat_pct": current["body_fat_pct"],
            "start_weight_lb": oldest["weight_lb"],
            "start_body_fat_pct": oldest["body_fat_pct"],
            "start_date": str(oldest["date"]),
            "weight_change_lb": weight_change,
            "body_fat_change_pct": fat_change,
            "days_tracked": len(rows),
            "history": rows,
        }

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

        if dataset_exists("daily_training_nutrition_context"):
            result["daily"] = run_query("""
                SELECT
                    d.date,
                    d.training_readiness,
                    d.hrv,
                    d.sleep_score,
                    ROUND((g.sleep_seconds / 3600.0)::numeric, 1) AS sleep_hours,
                    ROUND((g.deep_sleep_seconds / 60.0)::numeric) AS deep_sleep_min,
                    ROUND((g.rem_sleep_seconds / 60.0)::numeric) AS rem_sleep_min,
                    g.sleep_notes,
                    d.body_battery,
                    d.training_load,
                    d.acute_training_load,
                    d.chronic_training_load,
                    ROUND(
                        CASE
                            WHEN d.chronic_training_load > 0
                            THEN (d.acute_training_load / d.chronic_training_load)::numeric
                            ELSE NULL
                        END, 2
                    ) AS acwr,
                    d.training_status,
                    d.respiration_avg,
                    d.spo2_avg,
                    ROUND(d.nutrition_calories::numeric) AS calories,
                    ROUND(d.protein_g::numeric)          AS protein_g,
                    ROUND(d.carbs_g::numeric)            AS carbs_g,
                    ROUND(d.fat_g::numeric)              AS fat_g,
                    ROUND(d.ride_kj::numeric)            AS ride_kj,
                    d.ride_count,
                    ROUND(d.strength_volume_load::numeric) AS strength_volume_load,
                    ROUND(d.strength_avg_rpe::numeric, 1)  AS strength_avg_rpe
                FROM daily_training_nutrition_context d
                LEFT JOIN garmin_daily g ON g.date = d.date
                ORDER BY d.date DESC
                LIMIT %s
            """, (days,))
        else:
            result["daily"] = []

        if dataset_exists("hevy_weekly_volume"):
            result["weekly_strength"] = run_query("""
                select * from hevy_weekly_volume
                order by week_start desc
                limit %s
            """, (clamp_limit(weeks, 1, 12),))
        else:
            result["weekly_strength"] = []

        if dataset_exists("garmin_daily"):
            result["weekly_sleep"] = run_query("""
                SELECT
                    date_trunc('week', date)::date AS week_start,
                    count(*) AS days_with_data,
                    ROUND(avg(sleep_score)::numeric, 1) AS avg_sleep_score,
                    ROUND(avg(sleep_seconds / 3600.0)::numeric, 2) AS avg_sleep_hours,
                    ROUND(avg(deep_sleep_seconds / 60.0)::numeric) AS avg_deep_sleep_min,
                    ROUND(avg(rem_sleep_seconds / 60.0)::numeric) AS avg_rem_sleep_min,
                    ROUND(avg(hrv)::numeric, 1) AS avg_hrv,
                    ROUND(avg(resting_hr)::numeric, 1) AS avg_resting_hr,
                    string_agg(sleep_notes, ' | ' ORDER BY date)
                        FILTER (WHERE sleep_notes IS NOT NULL AND sleep_notes <> '') AS sleep_notes
                FROM garmin_daily
                WHERE date >= current_date - (%s * interval '1 week')
                  AND sleep_score IS NOT NULL
                GROUP BY date_trunc('week', date)
                ORDER BY week_start DESC
            """, (clamp_limit(weeks, 1, 12),))
        else:
            result["weekly_sleep"] = []

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
                SELECT {cols}
                FROM public.strava_activities
                WHERE sport_type IN ('Ride', 'VirtualRide', 'MountainBikeRide')
                ORDER BY activity_date DESC
                LIMIT 10
            """).format(cols=select_sql)
            result["recent_rides"] = run_query_composed(query)
        else:
            result["recent_rides"] = []

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
                    SELECT {cols}
                    FROM public.garmin_daily
                    ORDER BY date DESC
                    LIMIT 1
                """).format(cols=select_sql)
                rows = run_query_composed(query)
                result["acwr_summary"] = rows[0] if rows else {}
            else:
                result["acwr_summary"] = {}
        else:
            result["acwr_summary"] = {}

        return result
