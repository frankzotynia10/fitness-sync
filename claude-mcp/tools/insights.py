from db import run_query, relation_exists, dataset_exists


def register(mcp):

    @mcp.tool()
    def get_todays_context() -> dict:
        """Return today's complete snapshot: recovery signals (HRV, sleep, body battery,
        training readiness, resting HR, training load/status, weight), today's nutrition
        (calories, protein, carbs, fat), sleep notes if entered in Garmin Connect,
        and whether the user has trained or ridden today.
        This is the single best first call for any coaching or check-in conversation."""
        rows = run_query("""
            SELECT
                g.date,
                g.hrv,
                g.sleep_score,
                ROUND((g.sleep_seconds / 3600.0)::numeric, 1) AS sleep_hours,
                g.deep_sleep_seconds,
                g.rem_sleep_seconds,
                g.sleep_notes,
                g.body_battery,
                g.training_readiness,
                g.resting_hr,
                g.acute_training_load,
                g.chronic_training_load,
                ROUND(
                    CASE
                        WHEN g.chronic_training_load > 0
                        THEN (g.acute_training_load / g.chronic_training_load)::numeric
                        ELSE NULL
                    END, 2
                ) AS acwr,
                g.training_status,
                g.recovery_time_hours,
                ROUND((g.weight_kg * 2.20462)::numeric, 1) AS weight_lb,
                g.body_fat_pct,
                g.stress_avg,
                g.spo2_avg,
                n.calories,
                n.protein_g,
                n.carbs_g,
                n.fat_g,
                (SELECT COUNT(*) FROM hevy_workouts hw
                 WHERE hw.start_time::date = g.date) AS workouts_today,
                (SELECT COUNT(*) FROM strava_activities sa
                 WHERE sa.activity_date::date = g.date
                 AND sa.sport_type IN ('Ride', 'VirtualRide', 'EBikeRide')) AS rides_today,
                (SELECT COALESCE(SUM(sa.distance_m) / 1609.34, 0)
                 FROM strava_activities sa
                 WHERE sa.activity_date::date = g.date
                 AND sa.sport_type IN ('Ride', 'VirtualRide', 'EBikeRide')) AS ride_distance_mi_today
            FROM garmin_daily g
            LEFT JOIN daily_nutrition n ON n.date = g.date
            WHERE g.date = CURRENT_DATE
            LIMIT 1
        """)
        if not rows:
            return {"message": "No Garmin data for today yet."}
        return rows[0]

    @mcp.tool()
    def get_weekly_prs(weeks: int = 4) -> list:
        """Return estimated 1RM personal records set in the last N weeks.
        A PR is defined as a session where the estimated 1RM for an exercise
        exceeds all previous sessions for that exercise. Weights shown in both
        kg and lbs. Useful for spotting strength gains and celebrating progress."""
        if not relation_exists("hevy_exercise_progression"):
            return [{"message": "hevy_exercise_progression view does not exist yet."}]
        return run_query("""
            WITH history AS (
                SELECT
                    exercise_name,
                    workout_date,
                    best_estimated_1rm,
                    top_weight_kg,
                    avg_rpe,
                    MAX(best_estimated_1rm) OVER (
                        PARTITION BY exercise_name
                        ORDER BY workout_date
                        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                    ) AS prev_best_1rm
                FROM hevy_exercise_progression
                WHERE best_estimated_1rm IS NOT NULL
            )
            SELECT
                exercise_name,
                workout_date,
                ROUND(best_estimated_1rm::numeric, 1) AS estimated_1rm_kg,
                ROUND((best_estimated_1rm * 2.20462)::numeric, 1) AS estimated_1rm_lb,
                ROUND(top_weight_kg::numeric, 1) AS top_set_kg,
                ROUND((top_weight_kg * 2.20462)::numeric, 1) AS top_set_lb,
                ROUND(avg_rpe::numeric, 1) AS avg_rpe,
                ROUND(prev_best_1rm::numeric, 1) AS prev_best_1rm_kg,
                ROUND(((best_estimated_1rm - COALESCE(prev_best_1rm, 0)) * 2.20462)::numeric, 1) AS improvement_lb
            FROM history
            WHERE best_estimated_1rm > COALESCE(prev_best_1rm, 0)
              AND workout_date >= CURRENT_DATE - (%s * INTERVAL '1 week')
            ORDER BY workout_date DESC, exercise_name
        """, (weeks,))

    @mcp.tool()
    def get_ride_power_trend(limit: int = 20) -> list:
        """Return ride power trend over time \u2014 best 5-min and 20-min power, normalized
        power (NP), distance, and duration per ride. Useful for tracking FTP progression
        and cycling fitness over time. All power values in watts, distance in miles,
        duration in hours. Only includes outdoor/virtual rides with power data."""
        if not relation_exists("strava_power_curve_simple"):
            return [{"message": "strava_power_curve_simple view does not exist yet."}]
        return run_query("""
            SELECT
                sa.activity_date::date AS ride_date,
                sa.name AS ride_name,
                sa.sport_type,
                spc.best_5m_watts,
                spc.best_20m_watts,
                sa.weighted_average_watts AS normalized_power,
                sa.average_watts,
                sa.max_watts,
                ROUND((sa.distance_m / 1609.34)::numeric, 1) AS distance_mi,
                ROUND((sa.moving_time_s / 3600.0)::numeric, 2) AS duration_hr,
                sa.average_heartrate,
                sa.max_heartrate,
                CASE
                    WHEN sa.moving_time_s > 0 AND sa.average_watts > 0
                    THEN ROUND((sa.average_watts / (sa.distance_m / 1609.34))::numeric, 1)
                    ELSE NULL
                END AS watts_per_mile
            FROM strava_power_curve_simple spc
            JOIN strava_activities sa USING (strava_activity_id)
            WHERE sa.sport_type IN ('Ride', 'VirtualRide', 'EBikeRide')
            ORDER BY sa.activity_date DESC
            LIMIT %s
        """, (limit,))
