from db import run_query, run_query_one, dataset_exists, relation_exists
from utils import clamp_limit


def register(mcp):

    @mcp.tool()
    def get_nutrition_history(limit: int = 30) -> list:
        """Return recent nutrition history."""
        if not dataset_exists("daily_nutrition"):
            return [{"message": "daily_nutrition table does not exist yet."}]
        return run_query("select * from daily_nutrition order by date desc limit %s",
                         (clamp_limit(limit, 1, 90),))

    @mcp.tool()
    def get_nutrition_for_date(date_text: str) -> dict:
        """Return nutrition for a specific date (YYYY-MM-DD)."""
        if not dataset_exists("daily_nutrition"):
            return {"message": "daily_nutrition table does not exist yet."}
        return run_query_one("select * from daily_nutrition where date = %s limit 1", (date_text,))

    @mcp.tool()
    def get_recent_nutrition_recovery(limit: int = 14) -> list:
        """Return joined nutrition + recovery rows."""
        if not relation_exists("nutrition_recovery_daily"):
            return [{"message": "nutrition_recovery_daily view does not exist yet."}]
        return run_query("select * from nutrition_recovery_daily order by date desc limit %s",
                         (clamp_limit(limit, 1, 60),))

    @mcp.tool()
    def get_last_7_days_summary() -> list:
        """Return the last 7 days of joined recovery + nutrition data."""
        if not relation_exists("nutrition_recovery_daily"):
            return [{"message": "nutrition_recovery_daily view does not exist yet."}]
        return run_query("select * from nutrition_recovery_daily order by date desc limit 7")

    @mcp.tool()
    def get_daily_training_nutrition_context(days: int = 14) -> list:
        """Return combined Garmin + nutrition + Strava + Hevy daily context."""
        if not relation_exists("daily_training_nutrition_context"):
            return [{"message": "daily_training_nutrition_context view does not exist yet."}]
        return run_query("select * from daily_training_nutrition_context order by date desc limit %s",
                         (clamp_limit(days, 1, 180),))
