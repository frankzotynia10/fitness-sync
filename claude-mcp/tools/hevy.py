from db import run_query, run_query_one, dataset_exists, relation_exists
from utils import clamp_limit


def register(mcp):

    @mcp.tool()
    def get_current_hevy_routines(routine_title: str = "") -> list:
        """Return Hevy routine context. Optional filter by routine_title."""
        if not relation_exists("hevy_routine_context"):
            return [{"message": "hevy_routine_context view does not exist yet."}]
        if routine_title:
            return run_query(
                "select * from hevy_routine_context where routine_title = %s order by exercise_index, set_index",
                (routine_title,)
            )
        return run_query("select * from hevy_routine_context order by routine_title, exercise_index, set_index")

    @mcp.tool()
    def get_routine_summary() -> dict:
        """Return all Hevy routines in a single call with weights displayed in lbs.
        Organized by routine -> exercise -> sets. Use this instead of calling
        get_hevy_routine_detail four times."""
        if not relation_exists("hevy_routine_context"):
            return {"message": "hevy_routine_context view does not exist yet."}

        KG_TO_LBS = 2.20462262

        rows = run_query("""
            select
                routine_title,
                exercise_index,
                exercise_name,
                exercise_notes,
                rest_seconds,
                set_index,
                set_type,
                weight_kg,
                reps
            from hevy_routine_context
            order by routine_title, exercise_index, set_index
        """)

        routines = {}
        for row in rows:
            rt = row["routine_title"]
            if rt not in routines:
                routines[rt] = {}

            ex_idx = row["exercise_index"]
            if ex_idx not in routines[rt]:
                routines[rt][ex_idx] = {
                    "exercise_name":  row["exercise_name"],
                    "exercise_notes": row["exercise_notes"],
                    "rest_seconds":   row["rest_seconds"],
                    "sets":           [],
                }

            weight_kg = row["weight_kg"]
            weight_lbs = round(float(weight_kg) * KG_TO_LBS) if weight_kg is not None else None

            routines[rt][ex_idx]["sets"].append({
                "set_index":  row["set_index"],
                "set_type":   row["set_type"],
                "weight_lbs": weight_lbs,
                "weight_kg":  float(weight_kg) if weight_kg is not None else None,
                "reps":       row["reps"],
            })

        result = {}
        for routine_title, exercises in routines.items():
            result[routine_title] = [
                exercises[ex_idx]
                for ex_idx in sorted(exercises.keys())
            ]

        return result

    @mcp.tool()
    def get_hevy_routine_names() -> list:
        """Return available Hevy routine names."""
        if not dataset_exists("hevy_routines"):
            return [{"message": "hevy_routines table does not exist yet."}]
        return run_query("select routine_id, title, folder_id, created_at_source, updated_at_source from hevy_routines order by title")

    @mcp.tool()
    def get_hevy_routine_detail(routine_title: str) -> list:
        """Return one Hevy routine with exercises and sets."""
        if not relation_exists("hevy_routine_context"):
            return [{"message": "hevy_routine_context view does not exist yet."}]
        return run_query(
            "select * from hevy_routine_context where routine_title = %s order by exercise_index, set_index",
            (routine_title,)
        )

    @mcp.tool()
    def get_recent_hevy_workouts(limit: int = 20) -> list:
        """Return recent completed Hevy workouts."""
        if not dataset_exists("hevy_workouts"):
            return [{"message": "hevy_workouts table does not exist yet. Only routines are currently synced."}]
        return run_query("select * from hevy_workouts order by start_time desc limit %s",
                         (clamp_limit(limit, 1, 100),))

    @mcp.tool()
    def get_hevy_workout_detail(workout_id: str) -> list:
        """Return one completed Hevy workout with exercises and sets."""
        if relation_exists("hevy_workout_context"):
            return run_query(
                "select * from hevy_workout_context where workout_id = %s order by exercise_index, set_index",
                (workout_id,)
            )
        if dataset_exists("hevy_workout_sets"):
            return run_query(
                "select * from hevy_workout_sets where workout_id = %s order by exercise_index, set_index",
                (workout_id,)
            )
        return [{"message": "No completed Hevy workout detail tables/views exist yet."}]

    @mcp.tool()
    def get_weekly_strength_volume(weeks: int = 8) -> list:
        """Return weekly lifting volume / set count / total volume / avg RPE."""
        if not relation_exists("hevy_weekly_volume"):
            return [{"message": "hevy_weekly_volume view does not exist yet."}]
        return run_query("select * from hevy_weekly_volume order by week_start desc limit %s",
                         (clamp_limit(weeks, 1, 52),))

    @mcp.tool()
    def get_strength_progression(exercise_name: str, limit: int = 10) -> list:
        """Return progression history for a specific exercise. Supports partial matching."""
        if not relation_exists("hevy_exercise_progression"):
            return [{"message": "hevy_exercise_progression view does not exist yet."}]
        return run_query("""
            select * from hevy_exercise_progression
            where exercise_name ilike %s
            order by workout_date desc limit %s
        """, (f"%{exercise_name}%", clamp_limit(limit, 1, 100)))

    @mcp.tool()
    def get_muscle_group_fatigue(weeks: int = 6, muscle_group: str = "") -> list:
        """Return weekly fatigue by muscle group. Optional filter by primary_muscle_group."""
        if not relation_exists("hevy_muscle_group_fatigue"):
            return [{"message": "hevy_muscle_group_fatigue view does not exist yet."}]
        weeks = clamp_limit(weeks, 1, 52)
        if muscle_group:
            return run_query("""
                select * from hevy_muscle_group_fatigue
                where primary_muscle_group = %s
                order by week_start desc limit %s
            """, (muscle_group, weeks))
        return run_query(
            "select * from hevy_muscle_group_fatigue order by week_start desc, primary_muscle_group limit %s",
            (weeks * 20,)
        )

    @mcp.tool()
    def get_latest_day_context() -> dict:
        """Return the latest recovery + nutrition row plus the latest activity row."""
        latest_daily = latest_activity = None
        if relation_exists("nutrition_recovery_daily"):
            rows = run_query("select * from nutrition_recovery_daily order by date desc limit 1")
            latest_daily = rows[0] if rows else None
        if relation_exists("activity_recovery_daily"):
            rows = run_query("select * from activity_recovery_daily order by activity_date desc limit 1")
            latest_activity = rows[0] if rows else None
        return {"latest_daily": latest_daily, "latest_activity": latest_activity}

    @mcp.tool()
    def get_push_pull_legs_balance(weeks: int = 4) -> list:
        """Return weekly push/pull/legs volume balance. Shows sets and volume load
        per movement pattern per week. Useful for spotting muscle group imbalances
        and ensuring balanced programming across upper/lower body and push/pull patterns.
        Movement patterns: Push (chest/shoulders/triceps), Pull (back/biceps), Legs (quads/hamstrings/glutes/calves)."""
        if not dataset_exists("hevy_workout_sets"):
            return [{"message": "hevy_workout_sets table does not exist yet."}]
        if not dataset_exists("hevy_exercise_muscle_map"):
            return [{"message": "hevy_exercise_muscle_map table does not exist yet."}]

        return run_query("""
            WITH mapped AS (
                SELECT
                    hw.start_time::date AS workout_date,
                    date_trunc('week', hw.start_time)::date AS week_start,
                    hwe.title AS exercise_name,
                    hws.weight_kg,
                    hws.reps,
                    hws.set_type,
                    CASE
                        WHEN hemm.primary_muscle_group IN ('chest', 'shoulders', 'triceps') THEN 'Push'
                        WHEN hemm.primary_muscle_group IN ('back', 'biceps', 'lats', 'traps', 'rear_delts') THEN 'Pull'
                        WHEN hemm.primary_muscle_group IN ('quads', 'hamstrings', 'glutes', 'calves', 'abductors', 'adductors') THEN 'Legs'
                        WHEN hemm.primary_muscle_group IN ('abs', 'core') THEN 'Core'
                        ELSE 'Other'
                    END AS movement_pattern
                FROM hevy_workout_sets hws
                JOIN hevy_workouts hw ON hw.workout_id = hws.workout_id
                JOIN hevy_workout_exercises hwe ON hwe.workout_id = hws.workout_id
                    AND hwe.exercise_index = hws.exercise_index
                LEFT JOIN hevy_exercise_muscle_map hemm ON hemm.exercise_name = hwe.title
                WHERE hws.set_type = 'normal'
                  AND hw.start_time >= CURRENT_DATE - (%s * INTERVAL '1 week')
            )
            SELECT
                week_start,
                movement_pattern,
                COUNT(*) AS total_sets,
                ROUND(SUM(COALESCE(weight_kg, 0) * COALESCE(reps, 0) * 2.20462)::numeric, 0) AS volume_load_lb
            FROM mapped
            GROUP BY week_start, movement_pattern
            ORDER BY week_start DESC, movement_pattern
        """, (clamp_limit(weeks, 1, 52),))
