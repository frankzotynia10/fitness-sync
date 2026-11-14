from __future__ import annotations

import datetime

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

ALL_BLOCKS = (
    "stats", "sleep", "body_battery", "hrv", "readiness", "max_metrics", "stress",
    "endurance", "training_status", "recovery", "body",
)
# garminconnect blocks still needed when the official API is primary: they carry the
# fields the Personal API doesn't expose (training metrics, skin temp, activity
# seconds, BB at wake, heat acclimation, sleep notes). Official values win wherever
# both have one; garminconnect only fills gaps.
GAP_FILL_BLOCKS = ("stats", "sleep", "readiness", "max_metrics", "endurance", "training_status", "recovery")
# Fields both APIs have but define differently; keep garminconnect's value when present
# so history stays continuous. Official min/max HR is the wellness-only figure (1-7 bpm
# narrower); official floors are whole numbers (connect stores fractional floors).
CONNECT_PREFERRED = {"min_hr", "max_hr", "floors_ascended", "floors_descended",
                     "floors_ascended_m", "floors_descended_m"}
BODY_KEYS = ("weight_kg", "body_fat_pct", "bmi", "body_water", "bone_mass", "muscle_mass",
             "physique_rating", "visceral_fat", "metabolic_age")


def call_if_exists(client, method_name, *args):
    if hasattr(client, method_name):
        try:
            return getattr(client, method_name)(*args)
        except Exception as e:
            print(f"{method_name} failed: {e}")
    return None


# ═════════════════════════════════════════════════════════════
# garminconnect (unofficial)
# ═════════════════════════════════════════════════════════════
def connect_fields(client, target_date: datetime.date, blocks=ALL_BLOCKS) -> dict:
    day_str = target_date.isoformat()
    r: dict = {}

    if "stats" in blocks:
        try:
            stats = client.get_stats(day_str)
            dump_payload("stats", stats)
            r.update({
                "steps": stats.get("totalSteps"),
                "calories": stats.get("totalKilocalories"),
                "resting_hr": stats.get("restingHeartRate"),
                "active_kilocalories": stats.get("activeKilocalories"),
                "bmr_kilocalories": stats.get("bmrKilocalories"),
                "active_seconds": stats.get("activeSeconds"),
                "highly_active_seconds": stats.get("highlyActiveSeconds"),
                "sedentary_seconds": stats.get("sedentarySeconds"),
                "sleeping_seconds": stats.get("sleepingSeconds"),
                "distance_meters": stats.get("totalDistanceMeters"),
                "max_hr": stats.get("maxHeartRate"),
                "min_hr": stats.get("minHeartRate"),
                "body_battery_charged": stats.get("bodyBatteryChargedValue"),
                "body_battery_drained": stats.get("bodyBatteryDrainedValue"),
                "body_battery_high": stats.get("bodyBatteryHighestValue"),
                "body_battery_low": stats.get("bodyBatteryLowestValue"),
                "body_battery_at_wake": stats.get("bodyBatteryAtWakeTime"),
                "body_battery_during_sleep": stats.get("bodyBatteryDuringSleep"),
                "stress_duration_high": stats.get("highStressDuration"),
                "stress_duration_medium": stats.get("mediumStressDuration"),
                "stress_duration_low": stats.get("lowStressDuration"),
                "stress_duration_rest": stats.get("restStressDuration"),
                "stress_duration_uncategorized": stats.get("uncategorizedStressDuration"),
                "avg_skin_temp_deviation_c": stats.get("avgSkinTempDeviationC"),
                "floors_ascended": stats.get("floorsAscended"),
                "floors_descended": stats.get("floorsDescended"),
                "floors_ascended_m": stats.get("floorsAscendedInMeters"),
                "floors_descended_m": stats.get("floorsDescendedInMeters"),
            })
        except Exception as e:
            print(f"Stats fetch failed: {e}")

    if "sleep" in blocks:
        try:
            sleep_data = client.get_sleep_data(day_str)
            dump_payload("sleep_data", sleep_data)
            ds = sleep_data.get("dailySleepDTO", {})
            r.update({
                "sleep_seconds": ds.get("sleepTimeSeconds"),
                "sleep_notes": ds.get("userNote") or None,
                "sleep_stress_avg": ds.get("avgSleepStress"),
                "sleep_score": ds.get("sleepScores", {}).get("overall", {}).get("value"),
                "deep_sleep_seconds": first_non_null(ds.get("deepSleepSeconds"), ds.get("deepSleepDurationInSeconds"), recursive_find_first(sleep_data, {"deepSleepSeconds", "deepSleepDurationInSeconds"})),
                "light_sleep_seconds": first_non_null(ds.get("lightSleepSeconds"), ds.get("lightSleepDurationInSeconds"), recursive_find_first(sleep_data, {"lightSleepSeconds", "lightSleepDurationInSeconds"})),
                "rem_sleep_seconds": first_non_null(ds.get("remSleepSeconds"), ds.get("remSleepDurationInSeconds"), recursive_find_first(sleep_data, {"remSleepSeconds", "remSleepDurationInSeconds"})),
                "awake_seconds": first_non_null(ds.get("awakeSleepSeconds"), ds.get("awakeDurationInSeconds"), recursive_find_first(sleep_data, {"awakeSleepSeconds", "awakeDurationInSeconds"})),
            })
        except Exception as e:
            print(f"Sleep fetch failed: {e}")

    if "body_battery" in blocks:
        try:
            bb_data = client.get_body_battery(day_str)
            dump_payload("body_battery_data", bb_data)
            if isinstance(bb_data, list) and bb_data:
                values = bb_data[0].get("bodyBatteryValuesArray", [])
                if values:
                    r["body_battery"] = values[-1][1]
        except Exception as e:
            print(f"Body battery fetch failed: {e}")

    if "hrv" in blocks:
        try:
            hrv_data = client.get_hrv_data(day_str)
            dump_payload("hrv_data", hrv_data)
            r["hrv"] = _hrv_last_night(hrv_data)
        except Exception as e:
            print(f"HRV fetch failed: {e}")

    if "readiness" in blocks:
        try:
            readiness_data = client.get_training_readiness(day_str)
            dump_payload("training_readiness", readiness_data)
            if isinstance(readiness_data, list) and readiness_data:
                r["training_readiness"] = readiness_data[-1].get("score")
            elif isinstance(readiness_data, dict):
                r["training_readiness"] = readiness_data.get("score")
        except Exception as e:
            print(f"Training readiness fetch failed: {e}")

    if "max_metrics" in blocks:
        try:
            max_metrics = client.get_max_metrics(day_str)
            dump_payload("max_metrics", max_metrics)
            lm = (max_metrics[-1] if isinstance(max_metrics, list) and max_metrics
                  else max_metrics if isinstance(max_metrics, dict) else {})
            r["vo2_max"] = _vo2_from_entry(lm)
            r["heat_acclimation"] = (deep_get(lm, "heatAltitudeAcclimation", "heatAcclimationPercentage")
                                     or recursive_find_first(lm, {"heatAcclimationPercentage"}))
        except Exception as e:
            print(f"Max metrics fetch failed: {e}")

    if "stress" in blocks:
        try:
            stress_data = client.get_stress_data(day_str)
            dump_payload("stress_data", stress_data)
            r["stress_avg"] = stress_data.get("avgStressLevel")
            r["stress_max"] = stress_data.get("maxStressLevel")
        except Exception as e:
            print(f"Stress fetch failed: {e}")

    if "endurance" in blocks:
        try:
            week_start = (target_date - datetime.timedelta(days=target_date.weekday())).isoformat()
            endurance_data = client.get_endurance_score(week_start, day_str)
            dump_payload("endurance_score", endurance_data)
            if isinstance(endurance_data, dict):
                r["endurance_score"] = endurance_data.get("enduranceScoreDTO", {}).get("overallScore")
        except Exception as e:
            print(f"Endurance score fetch failed: {e}")

    if "training_status" in blocks:
        ts_data = (call_if_exists(client, "get_training_status", day_str)
                   or call_if_exists(client, "get_training_status_data", day_str))
        if ts_data:
            dump_payload("training_status_data", ts_data)
            latest_status_map = ts_data.get("mostRecentTrainingStatus", {}).get("latestTrainingStatusData", {})
            latest_status = next(iter(latest_status_map.values())) if isinstance(latest_status_map, dict) and latest_status_map else None
            if isinstance(latest_status, dict):
                acute_dto = latest_status.get("acuteTrainingLoadDTO", {})
                r["training_load"] = latest_status.get("weeklyTrainingLoad") or acute_dto.get("dailyTrainingLoadAcute")
                r["acute_training_load"] = acute_dto.get("dailyTrainingLoadAcute")
                r["chronic_training_load"] = acute_dto.get("dailyTrainingLoadChronic")
                r["training_status"] = (latest_status.get("trainingStatusFeedbackPhrase")
                                        or (str(latest_status.get("trainingStatus")) if latest_status.get("trainingStatus") is not None else None))
                r["acwr_ratio"] = acute_dto.get("dailyAcuteChronicWorkloadRatio")
                r["acwr_percent"] = acute_dto.get("acwrPercent")
            balance_map = ts_data.get("mostRecentTrainingLoadBalance", {}).get("metricsTrainingLoadBalanceDTOMap", {})
            latest_balance = next(iter(balance_map.values())) if isinstance(balance_map, dict) and balance_map else None
            if isinstance(latest_balance, dict):
                r["monthly_load_aerobic_low"] = latest_balance.get("monthlyLoadAerobicLow")
                r["monthly_load_aerobic_high"] = latest_balance.get("monthlyLoadAerobicHigh")
                r["monthly_load_anaerobic"] = latest_balance.get("monthlyLoadAnaerobic")
                r["training_balance_feedback"] = latest_balance.get("trainingBalanceFeedbackPhrase")

    if "recovery" in blocks:
        rt_data = (call_if_exists(client, "get_recovery_time", day_str)
                   or call_if_exists(client, "get_recovery_time_data", day_str))
        if rt_data:
            dump_payload("recovery_time_data", rt_data)
            raw_recovery = first_non_null(recursive_find_first(rt_data, {
                "recoveryTime", "recoveryTimeHrs", "recoveryHours", "recoveryTimeHours",
                "recoveryTimeInSeconds", "recoveryTimeSeconds", "recoveryTimeMillis", "remainingRecoveryTime",
            }))
            r["recovery_time_hours"] = normalize_recovery_time_hours(raw_recovery)

    if "body" in blocks:
        body: dict = {}
        try:
            wi_data = client.get_daily_weigh_ins(day_str)
            dump_payload("daily_weigh_ins", wi_data)
            entries = (wi_data.get("dailyWeighIns") or wi_data.get("weighIns") or
                       wi_data.get("dateWeightList") or wi_data.get("allMetrics") or []
                       ) if isinstance(wi_data, dict) else (wi_data if isinstance(wi_data, list) else [])
            if entries:
                _apply_body(body, entries[-1])
        except Exception as e:
            print(f"Daily weigh-ins fetch failed: {e}")
        try:
            sb_data = client.get_stats_and_body(day_str)
            dump_payload("stats_and_body", sb_data)
            _apply_body(body, sb_data)
            r.update({
                "respiration_avg": sb_data.get("avgWakingRespirationValue"),
                "spo2_avg": sb_data.get("averageSpo2"),
                "spo2_min": sb_data.get("lowestSpo2"),
                "intensity_minutes_moderate": sb_data.get("moderateIntensityMinutes"),
                "intensity_minutes_vigorous": sb_data.get("vigorousIntensityMinutes"),
            })
        except Exception as e:
            print(f"Stats/body fetch failed: {e}")
        r.update(body)

    return r


# ═════════════════════════════════════════════════════════════
# Personal API (official)
# ═════════════════════════════════════════════════════════════
def official_fields(off, target_date: datetime.date) -> dict:
    day = target_date.isoformat()
    r: dict = {}

    def ms_to_s(v):
        return int(round(v / 1000)) if v is not None else None

    try:
        s = off.daily_summary(day) or {}
        g = lambda *k: deep_get(s, *k)  # noqa: E731
        r.update({
            "steps": g("movement", "steps", "value"),
            "distance_meters": g("movement", "steps", "distanceInMeters"),
            "floors_ascended": g("movement", "floorsAscended", "value"),
            "floors_descended": g("movement", "floorsDescended", "value"),
            "floors_ascended_m": g("movement", "floorsAscended", "distanceInMeters"),
            "floors_descended_m": g("movement", "floorsDescended", "distanceInMeters"),
            "calories": g("calories", "burnedTotal"),
            "active_kilocalories": g("calories", "burnedActive"),
            "bmr_kilocalories": g("calories", "burnedResting"),
            "resting_hr": g("heartRate", "restingValue"),
            "min_hr": g("heartRate", "minValue"),
            "max_hr": g("heartRate", "maxValue"),
            "stress_avg": g("stress", "avgLevel"),
            "stress_max": g("stress", "maxLevel"),
            "stress_duration_high": ms_to_s(g("stress", "highStressDurationInMillis")),
            "stress_duration_medium": ms_to_s(g("stress", "mediumStressDurationInMillis")),
            "stress_duration_low": ms_to_s(g("stress", "lowStressDurationInMillis")),
            "stress_duration_rest": ms_to_s(g("stress", "restDurationInMillis")),
            "stress_duration_uncategorized": ms_to_s(g("stress", "uncategorizedDurationInMillis")),
            "body_battery": g("bodyBattery", "latestValue"),
            "body_battery_charged": g("bodyBattery", "chargedValue"),
            "body_battery_drained": g("bodyBattery", "drainedValue"),
            "body_battery_high": g("bodyBattery", "maxValue"),
            "body_battery_low": g("bodyBattery", "minValue"),
            "respiration_avg": g("respiration", "avgValue"),
            "spo2_avg": g("pulseOx", "avgValue"),
            "spo2_min": g("pulseOx", "minValue"),
            "intensity_minutes_moderate": g("intensityMinutes", "moderate"),
            "intensity_minutes_vigorous": g("intensityMinutes", "vigorous"),
        })
    except Exception as e:
        print(f"[official] daily summary failed: {e}")

    try:
        sl = off.daily_sleep(day) or {}
        r.update({
            "sleep_seconds": sl.get("sleepTimeSeconds"),
            "sleep_stress_avg": sl.get("avgSleepStress"),
            "sleep_score": deep_get(sl, "sleepScores", "overall", "value"),
            "deep_sleep_seconds": sl.get("deepSleepSeconds"),
            "light_sleep_seconds": sl.get("lightSleepSeconds"),
            "rem_sleep_seconds": sl.get("remSleepSeconds"),
            "awake_seconds": sl.get("awakeSleepSeconds"),
        })
    except Exception as e:
        print(f"[official] sleep failed: {e}")

    try:
        r["hrv"] = _hrv_last_night(off.get_hrv_data(day))
    except Exception as e:
        print(f"[official] HRV failed: {e}")

    try:
        vo2 = off.vo2_max(day, day) or []
        if vo2:
            r["vo2_max"] = _vo2_from_entry(vo2[-1])
    except Exception as e:
        print(f"[official] VO2 max failed: {e}")

    try:
        summaries = (off.weight_range(day, day) or {}).get("dailyWeightSummaries") or []
        entry = next((w for w in summaries if w.get("summaryDate") == day), None)
        body: dict = {}
        if entry and entry.get("latestWeight"):
            _apply_body(body, entry["latestWeight"])
        r.update({k: body.get(k) for k in BODY_KEYS})  # NULL on no weigh-in, like connect mode
    except Exception as e:
        print(f"[official] weight failed: {e}")

    return r


# ═════════════════════════════════════════════════════════════
# shared helpers
# ═════════════════════════════════════════════════════════════
def _hrv_last_night(hrv_data):
    hrv_data = hrv_data or {}
    hrv = (hrv_data.get("hrvSummary") or {}).get("lastNightAvg")
    if hrv is None:
        vals = [r.get("hrvValue") for r in hrv_data.get("hrvReadings") or [] if r.get("hrvValue") is not None]
        hrv = round(sum(vals) / len(vals), 2) if vals else None
    return hrv


def _vo2_from_entry(entry):
    return (deep_get(entry, "cycling", "vo2MaxValue")
            or deep_get(entry, "generic", "vo2MaxValue")
            or recursive_find_first(entry, {"vo2MaxValue"}))


def _apply_body(body: dict, src) -> None:
    """Fill body-composition keys in `body` from `src`, keeping values already set."""
    if not isinstance(src, dict):
        return
    candidates = {
        "weight_kg": normalize_weight_to_kg(src.get("weight") or src.get("weightKG") or src.get("weightKilograms")),
        "body_fat_pct": src.get("bodyFat") or src.get("percentFat") or src.get("bodyFatPercentage"),
        "bmi": src.get("bmi"),
        "body_water": normalize_percentage(src.get("bodyWater")),
        "bone_mass": normalize_mass_to_kg(src.get("boneMass")),
        "muscle_mass": normalize_mass_to_kg(src.get("muscleMass")),
        "physique_rating": src.get("physiqueRating"),
        "visceral_fat": src.get("visceralFat"),
        "metabolic_age": src.get("metabolicAge"),
    }
    for k, v in candidates.items():
        if body.get(k) is None:
            body[k] = v


def merge_official_first(official: dict, connect: dict) -> dict:
    """Official value wherever it has one; garminconnect fills the gaps
    (and wins for CONNECT_PREFERRED fields when it has a value)."""
    merged = dict(connect)
    for k, v in official.items():
        if k in CONNECT_PREFERRED and connect.get(k) is not None:
            continue
        if v is not None or k not in merged:
            merged[k] = v
    return merged


def upsert_daily(conn, target_date: datetime.date, fields: dict, existing_cols: set) -> None:
    row_data = {"date": target_date, **fields, "updated_at": datetime.datetime.now(datetime.timezone.utc)}
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


# ═════════════════════════════════════════════════════════════
# entry
# ═════════════════════════════════════════════════════════════
def sync_date(gc, off, target_date: datetime.date, conn, existing_cols: set) -> None:
    """Sync one day. `off` = OfficialClient or None; `gc` = garminconnect client or None.

    official mode (off set): official data first, garminconnect fills gaps if available.
    connect mode (off None): everything from garminconnect, as before.
    """
    day_str = target_date.isoformat()
    print(f"\n{'='*50}")
    print(f"Syncing {day_str} ({'official + connect gap-fill' if off and gc else 'official only' if off else 'connect'})...")
    print(f"{'='*50}")

    if off is not None:
        fields = official_fields(off, target_date)
        if gc is not None:
            fields = merge_official_first(fields, connect_fields(gc, target_date, GAP_FILL_BLOCKS))
    else:
        fields = connect_fields(gc, target_date)

    upsert_daily(conn, target_date, fields, existing_cols)
    print(f"Upserted {day_str} OK.")

    # Intraday: HR + HRV payloads are identical on both APIs, so take them from the
    # official client when we have it. The rest only exist on garminconnect.
    primary = off or gc
    sync_hr_intraday(primary, conn, day_str)
    sync_hrv_intraday(primary, conn, day_str)
    if gc is not None:
        sync_stress_bb_intraday(gc, conn, day_str)
        sync_steps_intraday(gc, conn, day_str)
        sync_respiration_intraday(gc, conn, day_str)
        sync_sleep_intraday(gc, conn, day_str)
    sync_activities(primary, conn, day_str, zones_client=gc)
    if gc is not None:
        sync_race_predictions(gc, conn, day_str)
