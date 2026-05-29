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
