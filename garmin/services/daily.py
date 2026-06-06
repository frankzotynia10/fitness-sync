from __future__ import annotations

import datetime
import sys

from psycopg2 import sql

from utils import (
    dump_payload, deep_get, first_non_null, recursive_find_first,
    normalize_weight_to_kg, normalize_mass_to_kg, normalize_percentage,
    normalize_recovery_time_hours,
)
from services.intraday import (
    sync_hr_intraday, sync_stress_bb_intraday, sync_steps_intraday,
    sync_respiration_intraday, sync_hrv_intraday, sync_sleep_intraday,
)
from services.activities import sync_activities
from services.race_predictions import sync_race_predictions


def call_if_exists(client, method_name, *args):
    if hasattr(client, method_name):
        try:
            return getattr(client, method_name)(*args)
        except Exception as e:
            print(f"{method_name} failed: {e}")
    return None


def sync_date(client, target_date: datetime.date, conn, existing_cols: set) -> None:
    day_str = target_date.isoformat()
    print(f"\n{'='*50}")
    print(f"Syncing {day_str}...")
    print(f"{'='*50}")

    # ── Stats ────────────────────────────────────────────────
    stats = client.get_stats(day_str)
    dump_payload("stats", stats)

    steps                     = stats.get("totalSteps")
    calories                  = stats.get("totalKilocalories")
    resting_hr                = stats.get("restingHeartRate")
    active_kilocalories       = stats.get("activeKilocalories")
    bmr_kilocalories          = stats.get("bmrKilocalories")
    active_seconds            = stats.get("activeSeconds")
    highly_active_seconds     = stats.get("highlyActiveSeconds")
    sedentary_seconds         = stats.get("sedentarySeconds")
    sleeping_seconds          = stats.get("sleepingSeconds")
    distance_meters           = stats.get("totalDistanceMeters")
    max_hr                    = stats.get("maxHeartRate")
    min_hr                    = stats.get("minHeartRate")
    body_battery_charged      = stats.get("bodyBatteryChargedValue")
    body_battery_drained      = stats.get("bodyBatteryDrainedValue")
    body_battery_high         = stats.get("bodyBatteryHighestValue")
    body_battery_low          = stats.get("bodyBatteryLowestValue")
    body_battery_at_wake      = stats.get("bodyBatteryAtWakeTime")
    body_battery_during_sleep = stats.get("bodyBatteryDuringSleep")
    stress_duration_high          = stats.get("highStressDuration")
    stress_duration_medium        = stats.get("mediumStressDuration")
    stress_duration_low           = stats.get("lowStressDuration")
    stress_duration_rest          = stats.get("restStressDuration")
    stress_duration_uncategorized = stats.get("uncategorizedStressDuration")
    avg_skin_temp_deviation_c = stats.get("avgSkinTempDeviationC")
    floors_ascended           = stats.get("floorsAscended")
    floors_descended          = stats.get("floorsDescended")
    floors_ascended_m         = stats.get("floorsAscendedInMeters")
    floors_descended_m        = stats.get("floorsDescendedInMeters")

    # ── Sleep ────────────────────────────────────────────────
    sleep_seconds = sleep_score = sleep_notes = None
    deep_sleep_seconds = light_sleep_seconds = rem_sleep_seconds = awake_seconds = sleep_stress_avg = None
    try:
        sleep_data = client.get_sleep_data(day_str)
        dump_payload("sleep_data", sleep_data)
        ds = sleep_data.get("dailySleepDTO", {})
        sleep_seconds   = ds.get("sleepTimeSeconds")
        sleep_notes     = ds.get("userNote") or None
        sleep_stress_avg = ds.get("avgSleepStress")
        sleep_score     = (ds.get("sleepScores", {}).get("overall", {}).get("value"))
        deep_sleep_seconds  = first_non_null(ds.get("deepSleepSeconds"), ds.get("deepSleepDurationInSeconds"), recursive_find_first(sleep_data, {"deepSleepSeconds", "deepSleepDurationInSeconds"}))
        light_sleep_seconds = first_non_null(ds.get("lightSleepSeconds"), ds.get("lightSleepDurationInSeconds"), recursive_find_first(sleep_data, {"lightSleepSeconds", "lightSleepDurationInSeconds"}))
        rem_sleep_seconds   = first_non_null(ds.get("remSleepSeconds"), ds.get("remSleepDurationInSeconds"), recursive_find_first(sleep_data, {"remSleepSeconds", "remSleepDurationInSeconds"}))
        awake_seconds       = first_non_null(ds.get("awakeSleepSeconds"), ds.get("awakeDurationInSeconds"), recursive_find_first(sleep_data, {"awakeSleepSeconds", "awakeDurationInSeconds"}))
    except Exception as e:
        print(f"Sleep fetch failed: {e}")

    # ── Body Battery ─────────────────────────────────────────
    body_battery = None
    try:
        bb_data = client.get_body_battery(day_str)
        dump_payload("body_battery_data", bb_data)
        if isinstance(bb_data, list) and bb_data:
            values = bb_data[0].get("bodyBatteryValuesArray", [])
            if values:
                body_battery = values[-1][1]
    except Exception as e:
        print(f"Body battery fetch failed: {e}")

    # ── HRV ──────────────────────────────────────────────────
    hrv = None
    try:
        hrv_data = client.get_hrv_data(day_str)
        dump_payload("hrv_data", hrv_data)
        hrv = hrv_data.get("hrvSummary", {}).get("lastNightAvg")
        if hrv is None:
            readings = hrv_data.get("hrvReadings", [])
            vals = [r.get("hrvValue") for r in readings if r.get("hrvValue") is not None]
            hrv = round(sum(vals) / len(vals), 2) if vals else None
    except Exception as e:
        print(f"HRV fetch failed: {e}")

    # ── Training Readiness ───────────────────────────────────
    training_readiness = None
    try:
        readiness_data = client.get_training_readiness(day_str)
        dump_payload("training_readiness", readiness_data)
        if isinstance(readiness_data, list) and readiness_data:
            training_readiness = readiness_data[-1].get("score")
        elif isinstance(readiness_data, dict):
            training_readiness = readiness_data.get("score")
    except Exception as e:
        print(f"Training readiness fetch failed: {e}")

    # ── VO2 Max / Heat Acclimation ───────────────────────────
    vo2_max = heat_acclimation = None
    try:
        max_metrics = client.get_max_metrics(day_str)
        dump_payload("max_metrics", max_metrics)
        lm = (max_metrics[-1] if isinstance(max_metrics, list) and max_metrics
              else max_metrics if isinstance(max_metrics, dict) else {})
        vo2_max = (deep_get(lm, "cycling", "vo2MaxValue")
                   or deep_get(lm, "generic", "vo2MaxValue")
                   or recursive_find_first(lm, {"vo2MaxValue"}))
        heat_acclimation = (deep_get(lm, "heatAltitudeAcclimation", "heatAcclimationPercentage")
                            or recursive_find_first(lm, {"heatAcclimationPercentage"}))
    except Exception as e:
        print(f"Max metrics fetch failed: {e}")

    # ── Stress ───────────────────────────────────────────────
    stress_avg = stress_max = None
    try:
        stress_data = client.get_stress_data(day_str)
        dump_payload("stress_data", stress_data)
        stress_avg = stress_data.get("avgStressLevel")
        stress_max = stress_data.get("maxStressLevel")
    except Exception as e:
        print(f"Stress fetch failed: {e}")

    # ── Endurance Score ──────────────────────────────────────
    endurance_score = None
    try:
        week_start = (target_date - datetime.timedelta(days=target_date.weekday())).isoformat()
        endurance_data = client.get_endurance_score(week_start, day_str)
        dump_payload("endurance_score", endurance_data)
        if isinstance(endurance_data, dict):
            endurance_score = endurance_data.get("enduranceScoreDTO", {}).get("overallScore")
    except Exception as e:
        print(f"Endurance score fetch failed: {e}")

    # ── Training Status / Load ───────────────────────────────
    training_load = acute_training_load = chronic_training_load = training_status = None
    acwr_ratio = acwr_percent = None
    monthly_load_aerobic_low = monthly_load_aerobic_high = monthly_load_anaerobic = training_balance_feedback = None
    ts_data = (call_if_exists(client, "get_training_status", day_str)
               or call_if_exists(client, "get_training_status_data", day_str))
    if ts_data:
        dump_payload("training_status_data", ts_data)
        latest_status_map = ts_data.get("mostRecentTrainingStatus", {}).get("latestTrainingStatusData", {})
        latest_status = next(iter(latest_status_map.values())) if isinstance(latest_status_map, dict) and latest_status_map else None
        if isinstance(latest_status, dict):
            acute_dto = latest_status.get("acuteTrainingLoadDTO", {})
            training_load = latest_status.get("weeklyTrainingLoad") or acute_dto.get("dailyTrainingLoadAcute")
            acute_training_load = acute_dto.get("dailyTrainingLoadAcute")
            chronic_training_load = acute_dto.get("dailyTrainingLoadChronic")
            training_status = (latest_status.get("trainingStatusFeedbackPhrase")
                               or (str(latest_status.get("trainingStatus")) if latest_status.get("trainingStatus") is not None else None))
            acwr_ratio = acute_dto.get("dailyAcuteChronicWorkloadRatio")
            acwr_percent = acute_dto.get("acwrPercent")
        balance_map = ts_data.get("mostRecentTrainingLoadBalance", {}).get("metricsTrainingLoadBalanceDTOMap", {})
        latest_balance = next(iter(balance_map.values())) if isinstance(balance_map, dict) and balance_map else None
        if isinstance(latest_balance, dict):
            monthly_load_aerobic_low  = latest_balance.get("monthlyLoadAerobicLow")
            monthly_load_aerobic_high = latest_balance.get("monthlyLoadAerobicHigh")
            monthly_load_anaerobic    = latest_balance.get("monthlyLoadAnaerobic")
            training_balance_feedback = latest_balance.get("trainingBalanceFeedbackPhrase")

    # ── Recovery Time ────────────────────────────────────────
    recovery_time_hours = None
    rt_data = (call_if_exists(client, "get_recovery_time", day_str)
               or call_if_exists(client, "get_recovery_time_data", day_str))
    if rt_data:
        dump_payload("recovery_time_data", rt_data)
        raw_recovery = first_non_null(recursive_find_first(rt_data, {
            "recoveryTime", "recoveryTimeHrs", "recoveryHours", "recoveryTimeHours",
            "recoveryTimeInSeconds", "recoveryTimeSeconds", "recoveryTimeMillis", "remainingRecoveryTime",
        }))
        recovery_time_hours = normalize_recovery_time_hours(raw_recovery)

    # ── Weight / Body Composition ────────────────────────────
    weight_kg = body_fat_pct = bmi = body_water = bone_mass = muscle_mass = None
    physique_rating = visceral_fat = metabolic_age = None

    def _apply_body(src):
        nonlocal weight_kg, body_fat_pct, bmi, body_water, bone_mass, muscle_mass
        nonlocal physique_rating, visceral_fat, metabolic_age
        if not isinstance(src, dict):
            return
        if weight_kg is None:
            weight_kg = normalize_weight_to_kg(src.get("weight") or src.get("weightKG") or src.get("weightKilograms"))
        if body_fat_pct is None:
            body_fat_pct = src.get("bodyFat") or src.get("percentFat") or src.get("bodyFatPercentage")
        if bmi is None:            bmi = src.get("bmi")
        if body_water is None:     body_water = normalize_percentage(src.get("bodyWater"))
        if bone_mass is None:      bone_mass = normalize_mass_to_kg(src.get("boneMass"))
        if muscle_mass is None:    muscle_mass = normalize_mass_to_kg(src.get("muscleMass"))
        if physique_rating is None: physique_rating = src.get("physiqueRating")
        if visceral_fat is None:   visceral_fat = src.get("visceralFat")
        if metabolic_age is None:  metabolic_age = src.get("metabolicAge")

    try:
        wi_data = client.get_daily_weigh_ins(day_str)
        dump_payload("daily_weigh_ins", wi_data)
        entries = (wi_data.get("dailyWeighIns") or wi_data.get("weighIns") or
                   wi_data.get("dateWeightList") or wi_data.get("allMetrics") or []
                   ) if isinstance(wi_data, dict) else (wi_data if isinstance(wi_data, list) else [])
        if entries:
            _apply_body(entries[-1])
    except Exception as e:
        print(f"Daily weigh-ins fetch failed: {e}")

    respiration_avg = spo2_avg = spo2_min = intensity_minutes_moderate = intensity_minutes_vigorous = None
    try:
        sb_data = client.get_stats_and_body(day_str)
        dump_payload("stats_and_body", sb_data)
        _apply_body(sb_data)
        respiration_avg = sb_data.get("avgWakingRespirationValue")
        spo2_avg = sb_data.get("averageSpo2")
        spo2_min = sb_data.get("lowestSpo2")
        intensity_minutes_moderate = sb_data.get("moderateIntensityMinutes")
        intensity_minutes_vigorous = sb_data.get("vigorousIntensityMinutes")
    except Exception as e:
        print(f"Stats/body fetch failed: {e}")

    # ── Upsert garmin_daily ──────────────────────────────────
    row_data = {
        "date": target_date,
        "steps": steps, "calories": calories, "resting_hr": resting_hr,
        "sleep_seconds": sleep_seconds, "sleep_notes": sleep_notes,
        "deep_sleep_seconds": deep_sleep_seconds, "light_sleep_seconds": light_sleep_seconds,
        "rem_sleep_seconds": rem_sleep_seconds, "awake_seconds": awake_seconds,
        "sleep_score": sleep_score, "sleep_stress_avg": sleep_stress_avg,
        "body_battery": body_battery, "training_readiness": training_readiness,
        "hrv": hrv, "vo2_max": vo2_max, "heat_acclimation": heat_acclimation,
        "endurance_score": endurance_score,
        "stress_avg": stress_avg, "stress_max": stress_max,
        "weight_kg": weight_kg, "body_fat_pct": body_fat_pct, "bmi": bmi,
        "body_water": body_water, "bone_mass": bone_mass, "muscle_mass": muscle_mass,
        "physique_rating": physique_rating, "visceral_fat": visceral_fat, "metabolic_age": metabolic_age,
        "recovery_time_hours": recovery_time_hours,
        "training_load": training_load, "acute_training_load": acute_training_load,
        "chronic_training_load": chronic_training_load, "training_status": training_status,
        "acwr_ratio": acwr_ratio, "acwr_percent": acwr_percent,
        "monthly_load_aerobic_low": monthly_load_aerobic_low,
        "monthly_load_aerobic_high": monthly_load_aerobic_high,
        "monthly_load_anaerobic": monthly_load_anaerobic,
        "training_balance_feedback": training_balance_feedback,
        "respiration_avg": respiration_avg, "spo2_avg": spo2_avg, "spo2_min": spo2_min,
        "intensity_minutes_moderate": intensity_minutes_moderate,
        "intensity_minutes_vigorous": intensity_minutes_vigorous,
        "active_kilocalories": active_kilocalories, "bmr_kilocalories": bmr_kilocalories,
        "active_seconds": active_seconds, "highly_active_seconds": highly_active_seconds,
        "sedentary_seconds": sedentary_seconds, "sleeping_seconds": sleeping_seconds,
        "distance_meters": distance_meters, "max_hr": max_hr, "min_hr": min_hr,
        "body_battery_charged": body_battery_charged, "body_battery_drained": body_battery_drained,
        "body_battery_high": body_battery_high, "body_battery_low": body_battery_low,
        "body_battery_at_wake": body_battery_at_wake, "body_battery_during_sleep": body_battery_during_sleep,
        "stress_duration_high": stress_duration_high, "stress_duration_medium": stress_duration_medium,
        "stress_duration_low": stress_duration_low, "stress_duration_rest": stress_duration_rest,
        "stress_duration_uncategorized": stress_duration_uncategorized,
        "avg_skin_temp_deviation_c": avg_skin_temp_deviation_c,
        "floors_ascended": floors_ascended, "floors_descended": floors_descended,
        "floors_ascended_m": floors_ascended_m, "floors_descended_m": floors_descended_m,
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
            cur.execute(
                sql.SQL("""
                    INSERT INTO public.garmin_daily ({insert_cols})
                    VALUES ({placeholders})
                    ON CONFLICT (date) DO UPDATE SET {updates}
                """).format(
                    insert_cols=insert_cols_sql,
                    placeholders=placeholders_sql,
                    updates=update_sql,
                ),
                values,
            )

    print(f"Upserted {day_str} OK.")

    # Intraday
    sync_hr_intraday(client, conn, day_str)
    sync_stress_bb_intraday(client, conn, day_str)
    sync_steps_intraday(client, conn, day_str)
    sync_respiration_intraday(client, conn, day_str)
    sync_hrv_intraday(client, conn, day_str)
    sync_sleep_intraday(client, conn, day_str)
    sync_activities(client, conn, day_str)
    sync_race_predictions(client, conn, day_str)
