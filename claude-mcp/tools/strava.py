from db import run_query, run_query_one, dataset_exists, relation_exists
from utils import clamp_limit


def register(mcp):

    @mcp.tool()
    def get_recent_strava_activities(limit: int = 30, sport_type: str = "") -> list:
        """Return recent Strava activities, optionally filtered by sport_type."""
        if not dataset_exists("strava_activities"):
            return [{"message": "strava_activities table does not exist yet."}]
        limit = clamp_limit(limit, 1, 200)
        if sport_type:
            return run_query(
                "select * from strava_activities where sport_type = %s order by activity_date desc limit %s",
                (sport_type, limit)
            )
        return run_query("select * from strava_activities order by activity_date desc limit %s", (limit,))

    @mcp.tool()
    def get_strava_activity_detail(strava_activity_id: int) -> dict:
        """Return one Strava activity by ID."""
        if not dataset_exists("strava_activities"):
            return {"message": "strava_activities table does not exist yet."}
        return run_query_one(
            "select * from strava_activities where strava_activity_id = %s limit 1",
            (strava_activity_id,)
        )

    @mcp.tool()
    def get_recent_activity_context(limit: int = 20) -> list:
        """Return recent activities joined with Garmin + nutrition context."""
        if not relation_exists("activity_recovery_daily"):
            return [{"message": "activity_recovery_daily view does not exist yet."}]
        return run_query(
            "select * from activity_recovery_daily order by activity_date desc limit %s",
            (clamp_limit(limit, 1, 100),)
        )

    @mcp.tool()
    def get_recent_ride_power_summary(limit: int = 20) -> list:
        """Return recent rides with power-related summary metrics."""
        if not dataset_exists("strava_activities"):
            return [{"message": "strava_activities table does not exist yet."}]
        return run_query("""
            select strava_activity_id, activity_date, name, sport_type,
                   distance_m, moving_time_s, elapsed_time_s,
                   average_speed, average_heartrate, max_heartrate,
                   average_watts, weighted_average_watts, max_watts, kilojoules
            from strava_activities
            where sport_type = 'Ride'
            order by activity_date desc limit %s
        """, (clamp_limit(limit, 1, 100),))

    @mcp.tool()
    def get_recent_power_curve(limit: int = 10) -> list:
        """Return recent rides with best 5-minute / 20-minute power and summary power metrics."""
        if not relation_exists("strava_power_curve_simple"):
            return [{"message": "strava_power_curve_simple view does not exist yet."}]
        return run_query(
            "select * from strava_power_curve_simple order by activity_date desc limit %s",
            (clamp_limit(limit, 1, 100),)
        )

    @mcp.tool()
    def get_activity_best_efforts(strava_activity_id: int) -> dict:
        """Return best-effort summary for a specific ride."""
        if not relation_exists("strava_activity_best_efforts"):
            return {"message": "strava_activity_best_efforts view does not exist yet."}
        return run_query_one(
            "select * from strava_activity_best_efforts where strava_activity_id = %s limit 1",
            (strava_activity_id,)
        )

    @mcp.tool()
    def get_activity_best_efforts_persisted(activity_id: int) -> list:
        """Return persisted best-effort power rows for one activity from activity_best_efforts."""
        if not relation_exists("activity_best_efforts"):
            return [{"message": "activity_best_efforts table does not exist yet."}]
        return run_query(
            "select * from activity_best_efforts where strava_activity_id = %s order by window_sec",
            (activity_id,)
        )

    @mcp.tool()
    def get_recent_best_power_for_rides(limit: int = 20, window_sec: int = 1200) -> list:
        """Return recent rides with persisted best-effort power for a given duration.
        Default is 20-minute best power."""
        if not relation_exists("activity_best_efforts"):
            return [{"message": "activity_best_efforts table does not exist yet."}]
        return run_query("""
            select sa.strava_activity_id, sa.activity_date, sa.name, sa.sport_type,
                   sa.distance_m, sa.moving_time_s, sa.elapsed_time_s,
                   sa.average_watts, sa.weighted_average_watts, sa.max_watts, sa.kilojoules,
                   abe.window_sec, abe.best_avg_power_w, abe.source, abe.computed_at
            from activity_best_efforts abe
            join strava_activities sa using (strava_activity_id)
            where abe.window_sec = %s
              and sa.sport_type = 'Ride'
            order by sa.activity_date desc
            limit %s
        """, (window_sec, clamp_limit(limit, 1, 100)))
