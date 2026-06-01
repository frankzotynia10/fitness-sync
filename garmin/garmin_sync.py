from garminconnect import Garmin
import psycopg2
from psycopg2 import sql
import datetime
import os
import sys
import json

TOKEN_DIR = os.environ.get("GARMINTOKENS", os.path.expanduser("~/.garminconnect"))

DB_HOST = os.environ["DB_HOST"]
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "postgres")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ["DB_PASSWORD"]

# Number of days back to re-sync on each run (catches late-finalizing data)
LOOKBACK_DAYS = int(os.environ.get("GARMIN_LOOKBACK_DAYS", "3"))


def dump_payload(label, payload, max_len=30000):
    print(f"\n--- {label} ---")
    try:
        text = json.dumps(payload, indent=2, default=str)
        if len(text) > max_len:
            print(text[:max_len] + "\n... [truncated] ...")
        else:
            print(text)
    except Exception as e:
        print(f"Could not dump {label}: {e}")


def normalize_weight_to_kg(raw_weight):
    if raw_weight is None:
        return None

    try:
        raw_weight = float(raw_weight)
    except Exception:
        return None

    # Garmin often returns grams for scale weight
    if raw_weight > 500:
        return raw_weight / 1000.0

    # Otherwise assume already kg
    return raw_weight


def normalize_mass_to_kg(raw_value):
    """
    Garmin body composition masses (boneMass / muscleMass) often come back in grams.
    Convert to kg if the number is too large to plausibly already be kg.
    """
    if raw_value is None:
        return None

    try:
        raw_value = float(raw_value)
    except Exception:
        return None

    if raw_value > 200:
        return raw_value / 1000.0

    return raw_value


def normalize_percentage(raw_value):
    if raw_value is None:
        return None

    try:
        return float(raw_value)
    except Exception:
        return None


def normalize_recovery_time_hours(raw_value):
    """
    Best-effort normalization:
    - if value looks like milliseconds -> convert to hours
    - if value looks like seconds -> convert to hours
    - if value looks like minutes -> convert to hours
    - else assume already hours
    """
    if raw_value is None:
        return None

    try:
        v = float(raw_value)
    except Exception:
        return None

    if v > 100000:
        return round(v / 3600000.0, 2)  # ms -> hr
    if v > 1000:
        return round(v / 3600.0, 2)     # sec -> hr
    if v > 72:
        return round(v / 60.0, 2)       # min -> hr

    return round(v, 2)


def deep_get(obj, *keys):
    cur = obj
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
        if cur is None:
            return None
    return cur


def first_non_null(*values):
    for v in values:
        if v is not None:
            return v
    return None


def recursive_find_first(obj, key_names):
    """
    Recursively search dict/list payloads for the first non-null matching key.
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in key_names and v is not None:
                return v
        for _, v in obj.items():
            found = recursive_find_first(v, key_names)
            if found is not None:
                return found

    elif isinstance(obj, list):
        for item in obj:
            found = recursive_find_first(item, key_names)
            if found is not None:
                return found

    return None


def call_if_exists(client, method_name, *args):
    if hasattr(client, method_name):
        method = getattr(client, method_name)
        try:
            return method(*args)
        except Exception as e:
            print(f"{method_name} failed: {e}")
            return None
    return None


def get_existing_columns(cur, table_name="garmin_daily", schema_name="public"):
    cur.execute(
        """
        select column_name
        from information_schema.columns
        where table_schema = %s
          and table_name = %s
        order by ordinal_position
        """,
        (schema_name, table_name)
    )
    return {row[0] for row in cur.fetchall()}


def sync_date(client, target_date, conn, existing_cols):
    day_str = target_date.isoformat()
    print(f"\n{'='*50}")
    print(f"Syncing {day_str}...")
    print(f"{'='*50}")

    # -------------------------------------------------
    # Initialize all fields we might write
    # -------------------------------------------------
    steps = None
    calories = None
    resting_hr = None

    sleep_seconds = None
    sleep_score = None
    deep_sleep_seconds = None
    light_sleep_seconds = None
    rem_sleep_seconds = None
    awake_seconds = None

    body_battery = None
    hrv = None
    training_readiness = None

    vo2_max = None
    heat_acclimation = None
    endurance_score = None

    stress_avg = None
    stress_max = None

    weight_kg = None
    body_fat_pct = None

    bmi = None
    body_water = None
    bone_mass = None
    muscle_mass = None
    physique_rating = None
    visceral_fat = None
    metabolic_age = None

    recovery_time_hours = None
    training_load = None
    acute_training_load = None
    chronic_training_load = None
    training_status = None

    respiration_avg = None
    spo2_avg = None
    spo2_min = None

    intensity_minutes_moderate = None
    intensity_minutes_vigorous = None

    acwr_ratio = None
    acwr_percent = None
    monthly_load_aerobic_low = None
    monthly_load_aerobic_high = None
    monthly_load_anaerobic = None
    training_balance_feedback = None

    # -------------------------------------------------
    # Basic daily stats
    # -------------------------------------------------
    stats = client.get_stats(day_str)
    dump_payload("stats", stats)

    steps = stats.get("totalSteps")
    calories = stats.get("totalKilocalories")
    resting_hr = stats.get("restingHeartRate")

    # -------------------------------------------------
    # Sleep + Sleep Score + Sleep Stages
    # -------------------------------------------------
    try:
        sleep_data = client.get_sleep_data(day_str)
        dump_payload("sleep_data", sleep_data)

        daily_sleep = sleep_data.get("dailySleepDTO", {})

        sleep_seconds = daily_sleep.get("sleepTimeSeconds")

        sleep_score = (
            daily_sleep.get("sleepScores", {})
            .get("overall", {})
            .get("value")
        )

        deep_sleep_seconds = first_non_null(
            daily_sleep.get("deepSleepSeconds"),
            daily_sleep.get("deepSleepDurationInSeconds"),
            recursive_find_first(sleep_data, {"deepSleepSeconds", "deepSleepDurationInSeconds"})
        )

        light_sleep_seconds = first_non_null(
            daily_sleep.get("lightSleepSeconds"),
            daily_sleep.get("lightSleepDurationInSeconds"),
            recursive_find_first(sleep_data, {"lightSleepSeconds", "lightSleepDurationInSeconds"})
        )

        rem_sleep_seconds = first_non_null(
            daily_sleep.get("remSleepSeconds"),
            daily_sleep.get("remSleepDurationInSeconds"),
            recursive_find_first(sleep_data, {"remSleepSeconds", "remSleepDurationInSeconds"})
        )

        awake_seconds = first_non_null(
            daily_sleep.get("awakeSleepSeconds"),
            daily_sleep.get("awakeDurationInSeconds"),
            recursive_find_first(sleep_data, {"awakeSleepSeconds", "awakeDurationInSeconds"})
        )

    except Exception as e:
        print(f"Sleep fetch failed: {e}")

    # -------------------------------------------------
    # Body Battery
    # -------------------------------------------------
    try:
        body_battery_data = client.get_body_battery(day_str)
        dump_payload("body_battery_data", body_battery_data)

        if isinstance(body_battery_data, list) and body_battery_data:
            first_entry = body_battery_data[0]
            values = first_entry.get("bodyBatteryValuesArray", [])

            if values:
                body_battery = values[-1][1]

    except Exception as e:
        print(f"Body battery fetch failed: {e}")

    # -------------------------------------------------
    # HRV
    # -------------------------------------------------
    try:
        hrv_data = client.get_hrv_data(day_str)
        dump_payload("hrv_data", hrv_data)

        hrv_summary = hrv_data.get("hrvSummary", {})
        hrv = hrv_summary.get("lastNightAvg")

        if hrv is None:
            readings = hrv_data.get("hrvReadings", [])
            if readings:
                hrv_values = [
                    r.get("hrvValue")
                    for r in readings
                    if r.get("hrvValue") is not None
                ]
                hrv = round(sum(hrv_values) / len(hrv_values), 2) if hrv_values else None

    except Exception as e:
        print(f"HRV fetch failed: {e}")

    # -------------------------------------------------
    # Training Readiness
    # -------------------------------------------------
    try:
        readiness_data = client.get_training_readiness(day_str)
        dump_payload("training_readiness", readiness_data)

        if isinstance(readiness_data, list) and readiness_data:
            training_readiness = readiness_data[-1].get("score")
        elif isinstance(readiness_data, dict):
            training_readiness = readiness_data.get("score")

    except Exception as e:
        print(f"Training readiness fetch failed: {e}")

    # -------------------------------------------------
    # VO2 Max + Heat Acclimation
    # -------------------------------------------------
    try:
        max_metrics = client.get_max_metrics(day_str)
        dump_payload("max_metrics", max_metrics)

        if isinstance(max_metrics, list) and max_metrics:
            latest_metric = max_metrics[-1]
        elif isinstance(max_metrics, dict):
            latest_metric = max_metrics
        else:
            latest_metric = {}

        vo2_max = (
            deep_get(latest_metric, "cycling", "vo2MaxValue")
            or deep_get(latest_metric, "generic", "vo2MaxValue")
            or recursive_find_first(latest_metric, {"vo2MaxValue"})
        )

        heat_acclimation = (
            deep_get(latest_metric, "heatAltitudeAcclimation", "heatAcclimationPercentage")
            or recursive_find_first(latest_metric, {"heatAcclimationPercentage"})
        )

    except Exception as e:
        print(f"Max metrics fetch failed: {e}")

    # -------------------------------------------------
    # Stress
    # -------------------------------------------------
    try:
        stress_data = client.get_stress_data(day_str)
        dump_payload("stress_data", stress_data)

        stress_avg = stress_data.get("avgStressLevel")
        stress_max = stress_data.get("maxStressLevel")

    except Exception as e:
        print(f"Stress fetch failed: {e}")

    # -------------------------------------------------
    # Endurance Score
    # -------------------------------------------------
    try:
        week_start = target_date - datetime.timedelta(days=target_date.weekday())
        week_start_str = week_start.isoformat()

        endurance_data = client.get_endurance_score(week_start_str, day_str)
        dump_payload("endurance_score", endurance_data)

        if isinstance(endurance_data, dict):
            endurance_score = (
                endurance_data.get("enduranceScoreDTO", {})
                .get("overallScore")
            )

    except Exception as e:
        print(f"Endurance score fetch failed: {e}")

    # -------------------------------------------------
    # Training Load / Status
    # -------------------------------------------------
    training_status_data = (
        call_if_exists(client, "get_training_status", day_str)
        or call_if_exists(client, "get_training_status_data", day_str)
    )

    if training_status_data is not None:
        dump_payload("training_status_data", training_status_data)

        recent_status = training_status_data.get("mostRecentTrainingStatus", {})
        latest_status_map = recent_status.get("latestTrainingStatusData", {})

        latest_status = None
        if isinstance(latest_status_map, dict) and latest_status_map:
            latest_status = next(iter(latest_status_map.values()))

        if isinstance(latest_status, dict):
            acute_dto = latest_status.get("acuteTrainingLoadDTO", {})

            weekly_training_load = latest_status.get("weeklyTrainingLoad")

            acute_training_load = acute_dto.get("dailyTrainingLoadAcute")
            chronic_training_load = acute_dto.get("dailyTrainingLoadChronic")

            training_load = weekly_training_load if weekly_training_load is not None else acute_training_load

            training_status = (
                latest_status.get("trainingStatusFeedbackPhrase")
                or (
                    str(latest_status.get("trainingStatus"))
                    if latest_status.get("trainingStatus") is not None
                    else None
                )
            )

            acwr_ratio = acute_dto.get("dailyAcuteChronicWorkloadRatio")
            acwr_percent = acute_dto.get("acwrPercent")

        balance = training_status_data.get("mostRecentTrainingLoadBalance", {})
        balance_map = balance.get("metricsTrainingLoadBalanceDTOMap", {})

        latest_balance = None
        if isinstance(balance_map, dict) and balance_map:
            latest_balance = next(iter(balance_map.values()))

        if isinstance(latest_balance, dict):
            monthly_load_aerobic_low = latest_balance.get("monthlyLoadAerobicLow")
            monthly_load_aerobic_high = latest_balance.get("monthlyLoadAerobicHigh")
            monthly_load_anaerobic = latest_balance.get("monthlyLoadAnaerobic")
            training_balance_feedback = latest_balance.get("trainingBalanceFeedbackPhrase")

    # -------------------------------------------------
    # Recovery Time
    # -------------------------------------------------
    recovery_time_data = (
        call_if_exists(client, "get_recovery_time", day_str)
        or call_if_exists(client, "get_recovery_time_data", day_str)
    )

    if recovery_time_data is not None:
        dump_payload("recovery_time_data", recovery_time_data)

        raw_recovery = first_non_null(
            recursive_find_first(recovery_time_data, {
                "recoveryTime",
                "recoveryTimeHrs",
                "recoveryHours",
                "recoveryTimeHours",
                "recoveryTimeInSeconds",
                "recoveryTimeSeconds",
                "recoveryTimeMillis",
                "remainingRecoveryTime"
            })
        )

        recovery_time_hours = normalize_recovery_time_hours(raw_recovery)

    # -------------------------------------------------
    # Daily weigh-ins (preferred source for scale/body-comp)
    # -------------------------------------------------
    try:
        weigh_ins_data = client.get_daily_weigh_ins(day_str)
        dump_payload("daily_weigh_ins", weigh_ins_data)

        if isinstance(weigh_ins_data, dict):
            entries = (
                weigh_ins_data.get("dailyWeighIns")
                or weigh_ins_data.get("weighIns")
                or weigh_ins_data.get("dateWeightList")
                or weigh_ins_data.get("allMetrics")
                or []
            )
        elif isinstance(weigh_ins_data, list):
            entries = weigh_ins_data
        else:
            entries = []

        if entries:
            latest = entries[-1]

            raw_weight = (
                latest.get("weight")
                or latest.get("weightKG")
                or latest.get("weightKilograms")
            )
            weight_kg = normalize_weight_to_kg(raw_weight)

            body_fat_pct = (
                latest.get("bodyFat")
                or latest.get("percentFat")
                or latest.get("bodyFatPercentage")
            )

            bmi = latest.get("bmi")
            body_water = normalize_percentage(latest.get("bodyWater"))
            bone_mass = normalize_mass_to_kg(latest.get("boneMass"))
            muscle_mass = normalize_mass_to_kg(latest.get("muscleMass"))
            physique_rating = latest.get("physiqueRating")
            visceral_fat = latest.get("visceralFat")
            metabolic_age = latest.get("metabolicAge")

    except Exception as e:
        print(f"Daily weigh-ins fetch failed: {e}")

    # -------------------------------------------------
    # stats_and_body fallback + respiration/spo2/intensity minutes
    # -------------------------------------------------
    try:
        stats_and_body = client.get_stats_and_body(day_str)
        dump_payload("stats_and_body", stats_and_body)

        if isinstance(stats_and_body, dict):
            if weight_kg is None:
                raw_weight = (
                    stats_and_body.get("weight")
                    or stats_and_body.get("weightKG")
                    or stats_and_body.get("weightKilograms")
                )
                weight_kg = normalize_weight_to_kg(raw_weight)

            if body_fat_pct is None:
                body_fat_pct = (
                    stats_and_body.get("bodyFat")
                    or stats_and_body.get("percentFat")
                    or stats_and_body.get("bodyFatPercentage")
                )

            if bmi is None:
                bmi = stats_and_body.get("bmi")
            if body_water is None:
                body_water = normalize_percentage(stats_and_body.get("bodyWater"))
            if bone_mass is None:
                bone_mass = normalize_mass_to_kg(stats_and_body.get("boneMass"))
            if muscle_mass is None:
                muscle_mass = normalize_mass_to_kg(stats_and_body.get("muscleMass"))
            if physique_rating is None:
                physique_rating = stats_and_body.get("physiqueRating")
            if visceral_fat is None:
                visceral_fat = stats_and_body.get("visceralFat")
            if metabolic_age is None:
                metabolic_age = stats_and_body.get("metabolicAge")

            respiration_avg = stats_and_body.get("avgWakingRespirationValue")
            spo2_avg = stats_and_body.get("averageSpo2")
            spo2_min = stats_and_body.get("lowestSpo2")

            intensity_minutes_moderate = stats_and_body.get("moderateIntensityMinutes")
            intensity_minutes_vigorous = stats_and_body.get("vigorousIntensityMinutes")

    except Exception as e:
        print(f"Stats/body fetch failed: {e}")

    # -------------------------------------------------
    # Build row payload and upsert
    # -------------------------------------------------
    row_data = {
        "date": target_date,
        "steps": steps,
        "calories": calories,
        "resting_hr": resting_hr,
        "sleep_seconds": sleep_seconds,
        "body_battery": body_battery,
        "training_readiness": training_readiness,
        "hrv": hrv,
        "vo2_max": vo2_max,
        "sleep_score": sleep_score,
        "stress_avg": stress_avg,
        "stress_max": stress_max,
        "endurance_score": endurance_score,
        "heat_acclimation": heat_acclimation,
        "weight_kg": weight_kg,
        "body_fat_pct": body_fat_pct,
        "bmi": bmi,
        "body_water": body_water,
        "bone_mass": bone_mass,
        "muscle_mass": muscle_mass,
        "physique_rating": physique_rating,
        "visceral_fat": visceral_fat,
        "metabolic_age": metabolic_age,
        "deep_sleep_seconds": deep_sleep_seconds,
        "light_sleep_seconds": light_sleep_seconds,
        "rem_sleep_seconds": rem_sleep_seconds,
        "awake_seconds": awake_seconds,
        "recovery_time_hours": recovery_time_hours,
        "training_load": training_load,
        "acute_training_load": acute_training_load,
        "chronic_training_load": chronic_training_load,
        "training_status": training_status,
        "respiration_avg": respiration_avg,
        "spo2_avg": spo2_avg,
        "spo2_min": spo2_min,
        "intensity_minutes_moderate": intensity_minutes_moderate,
        "intensity_minutes_vigorous": intensity_minutes_vigorous,
        "acwr_ratio": acwr_ratio,
        "acwr_percent": acwr_percent,
        "monthly_load_aerobic_low": monthly_load_aerobic_low,
        "monthly_load_aerobic_high": monthly_load_aerobic_high,
        "monthly_load_anaerobic": monthly_load_anaerobic,
        "training_balance_feedback": training_balance_feedback,
        "updated_at": datetime.datetime.now(datetime.timezone.utc),
    }

    with conn:
        with conn.cursor() as cur:
            filtered = {k: v for k, v in row_data.items() if k in existing_cols}

            if "date" not in filtered:
                raise RuntimeError("garmin_daily must contain a date column.")

            col_names = list(filtered.keys())
            values = [filtered[c] for c in col_names]

            insert_cols_sql = sql.SQL(", ").join(sql.Identifier(c) for c in col_names)
            placeholders_sql = sql.SQL(", ").join(sql.Placeholder() for _ in col_names)

            update_cols = [c for c in col_names if c != "date"]
            update_sql = sql.SQL(", ").join(
                sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(c), sql.Identifier(c))
                for c in update_cols
            )

            query = sql.SQL("""
                INSERT INTO public.garmin_daily ({insert_cols})
                VALUES ({placeholders})
                ON CONFLICT (date) DO UPDATE SET
                {updates}
            """).format(
                insert_cols=insert_cols_sql,
                placeholders=placeholders_sql,
                updates=update_sql
            )

            cur.execute(query, values)

    print(f"Upserted {day_str} OK.")


def main():
    today = datetime.date.today()

    print(f"Using token directory: {TOKEN_DIR}")
    print(f"Syncing last {LOOKBACK_DAYS} days (today + {LOOKBACK_DAYS - 1} prior)...")

    try:
        client = Garmin()
        client.login(tokenstore=TOKEN_DIR)

        print("Garmin methods containing 'recovery':")
        print([m for m in dir(client) if "recovery" in m.lower()])

        print("Garmin methods containing body/weight/weigh:")
        print([m for m in dir(client) if any(x in m.lower() for x in ["body", "weight", "weigh"])])

        print("Connecting to Postgres...")
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )

        with conn.cursor() as cur:
            existing_cols = get_existing_columns(cur, "garmin_daily", "public")

        dates_to_sync = [today - datetime.timedelta(days=i) for i in range(LOOKBACK_DAYS)]

        for target_date in dates_to_sync:
            try:
                sync_date(client, target_date, conn, existing_cols)
            except Exception as e:
                print(f"Failed to sync {target_date.isoformat()}: {e}", file=sys.stderr)

        conn.close()
        print("\nAll dates synced.")

    except Exception as e:
        print(f"Sync failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
    sys.exit(0)
