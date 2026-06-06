from garminconnect import Garmin
import psycopg2
from psycopg2 import sql
import datetime
import os
import sys
import json
import io
import zipfile

try:
    from fitparse import FitFile, FitParseError
    HAS_FITPARSE = True
except ImportError:
    HAS_FITPARSE = False
    print("WARNING: fitparse not installed — GPS/FIT sync will be skipped. pip install fitparse")

TOKEN_DIR = os.environ.get("GARMINTOKENS", os.path.expanduser("~/.garminconnect"))

DB_HOST = os.environ["DB_HOST"]
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "postgres")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ["DB_PASSWORD"]

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


def validate_token_dir():
    if not os.path.isdir(TOKEN_DIR):
        raise RuntimeError(
            f"GARMIN TOKEN ERROR: Token directory does not exist: {TOKEN_DIR}. "
            "Re-run login_once.py to re-authenticate."
        )
    token_files = [f for f in os.listdir(TOKEN_DIR) if not f.startswith('.')]
    if not token_files:
        raise RuntimeError(
            f"GARMIN TOKEN ERROR: Token directory is empty: {TOKEN_DIR}. "
            "Re-run login_once.py to re-authenticate."
        )
    print(f"Token directory OK: {TOKEN_DIR} ({len(token_files)} file(s): {token_files})")


def validate_garmin_session(client):
    try:
        name = client.get_full_name()
        print(f"Garmin session valid — authenticated as: {name}")
    except Exception as e:
        raise RuntimeError(
            f"GARMIN TOKEN ERROR: Login succeeded but session is invalid: {e}. "
            "Token may be expired. Re-run login_once.py to re-authenticate."
        )


def normalize_weight_to_kg(raw_weight):
    if raw_weight is None:
        return None
    try:
        raw_weight = float(raw_weight)
    except Exception:
        return None
    if raw_weight > 500:
        return raw_weight / 1000.0
    return raw_weight


def normalize_mass_to_kg(raw_value):
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
    if raw_value is None:
        return None
    try:
        v = float(raw_value)
    except Exception:
        return None
    if v > 100000:
        return round(v / 3600000.0, 2)
    if v > 1000:
        return round(v / 3600.0, 2)
    if v > 72:
        return round(v / 60.0, 2)
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


def ts_from_ms(ms_value):
    """Convert millisecond epoch to UTC datetime."""
    if ms_value is None:
        return None
    try:
        return datetime.datetime.fromtimestamp(ms_value / 1000.0, tz=datetime.timezone.utc)
    except Exception:
        return None


def parse_gmt_str(s, fmt="%Y-%m-%dT%H:%M:%S.%f"):
    """Parse a Garmin GMT string to UTC-aware datetime."""
    if not s:
        return None
    try:
        dt = datetime.datetime.strptime(s, fmt)
        return dt.replace(tzinfo=datetime.timezone.utc)
    except Exception:
        try:
            dt = datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
            return dt.replace(tzinfo=datetime.timezone.utc)
        except Exception:
            return None


# ── FTP sync ────────────────────────────────────────────────────────────────────────────
def sync_ftp(client, conn):
    print("\nSyncing cycling FTP...")
    try:
        data = client.get_cycling_ftp()
        dump_payload("cycling_ftp", data)

        ftp_watts   = data.get("functionalThresholdPower")
        sport       = data.get("sport", "CYCLING")
        source      = data.get("biometricSourceType")
        calendar_dt = data.get("calendarDate")

        if ftp_watts is None:
            print("  No FTP value returned, skipping.")
            return

        if calendar_dt:
            ftp_date = datetime.date.fromisoformat(str(calendar_dt)[:10])
        else:
            ftp_date = datetime.date.today()

        threshold_hr_cycling = None
        try:
            lt_data = client.get_lactate_threshold()
            threshold_hr_cycling = deep_get(lt_data, "speed_and_heart_rate", "heartRateCycling")
        except Exception as e:
            print(f"  Lactate threshold fetch failed (non-fatal): {e}")

        power_to_weight = None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT weight_kg FROM garmin_daily WHERE weight_kg IS NOT NULL ORDER BY date DESC LIMIT 1"
                )
                row = cur.fetchone()
                if row and row[0] and ftp_watts:
                    power_to_weight = round(ftp_watts / float(row[0]), 3)
                    print(f"  W/kg calculated from DB weight: {ftp_watts}W / {row[0]}kg = {power_to_weight}")
        except Exception as e:
            print(f"  W/kg calculation failed (non-fatal): {e}")

        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO public.garmin_ftp
                        (date, ftp_watts, power_to_weight, threshold_hr_cycling, sport, source, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (date) DO UPDATE SET
                        ftp_watts            = EXCLUDED.ftp_watts,
                        power_to_weight      = EXCLUDED.power_to_weight,
                        threshold_hr_cycling = EXCLUDED.threshold_hr_cycling,
                        sport                = EXCLUDED.sport,
                        source               = EXCLUDED.source,
                        updated_at           = NOW()
                    """,
                    (ftp_date, ftp_watts, power_to_weight, threshold_hr_cycling, sport, source)
                )
        print(f"  FTP upserted: {ftp_watts}W on {ftp_date} (W/kg: {power_to_weight})")
    except Exception as e:
        print(f"  FTP sync failed (non-fatal): {e}")


# ── Intraday: HR ───────────────────────────────────────────────────────────────────────
def sync_hr_intraday(client, conn, day_str):
    try:
        data = client.get_heart_rates(day_str)
        hr_values = data.get("heartRateValues") or []
        rows = [(ts_from_ms(entry[0]), entry[1]) for entry in hr_values if entry[1]]
        if not rows:
            return
        with conn:
            with conn.cursor() as cur:
                for recorded_at, hr in rows:
                    cur.execute(
                        """
                        INSERT INTO garmin_hr_intraday (recorded_at, heart_rate)
                        VALUES (%s, %s)
                        ON CONFLICT (recorded_at) DO UPDATE SET heart_rate = EXCLUDED.heart_rate
                        """,
                        (recorded_at, hr)
                    )
        print(f"  HR intraday: {len(rows)} points")
    except Exception as e:
        print(f"  HR intraday failed (non-fatal): {e}")


# ── Intraday: Stress + Body Battery ──────────────────────────────────────────────────────
def sync_stress_bb_intraday(client, conn, day_str):
    try:
        data = client.get_stress_data(day_str)
        stress_rows = []
        for entry in (data.get("stressValuesArray") or []):
            if entry[1] is not None and entry[1] >= 0:
                stress_rows.append((ts_from_ms(entry[0]), entry[1]))

        bb_rows = []
        for entry in (data.get("bodyBatteryValuesArray") or []):
            if len(entry) >= 3 and entry[2] is not None:
                bb_rows.append((ts_from_ms(entry[0]), entry[2]))

        with conn:
            with conn.cursor() as cur:
                for recorded_at, stress in stress_rows:
                    cur.execute(
                        """
                        INSERT INTO garmin_stress_intraday (recorded_at, stress_level)
                        VALUES (%s, %s)
                        ON CONFLICT (recorded_at) DO UPDATE SET stress_level = EXCLUDED.stress_level
                        """,
                        (recorded_at, stress)
                    )
                for recorded_at, bb in bb_rows:
                    cur.execute(
                        """
                        INSERT INTO garmin_bb_intraday (recorded_at, body_battery)
                        VALUES (%s, %s)
                        ON CONFLICT (recorded_at) DO UPDATE SET body_battery = EXCLUDED.body_battery
                        """,
                        (recorded_at, bb)
                    )
        print(f"  Stress intraday: {len(stress_rows)} points, BB intraday: {len(bb_rows)} points")
    except Exception as e:
        print(f"  Stress/BB intraday failed (non-fatal): {e}")


# ── Intraday: Steps ─────────────────────────────────────────────────────────────────────────────
def sync_steps_intraday(client, conn, day_str):
    try:
        steps_list = client.get_steps_data(day_str) or []
        rows = []
        for entry in steps_list:
            if entry.get("steps") is not None:
                ts = parse_gmt_str(entry.get("startGMT"))
                if ts:
                    rows.append((ts, entry["steps"]))
        if not rows:
            return
        with conn:
            with conn.cursor() as cur:
                for recorded_at, steps in rows:
                    cur.execute(
                        """
                        INSERT INTO garmin_steps_intraday (recorded_at, steps)
                        VALUES (%s, %s)
                        ON CONFLICT (recorded_at) DO UPDATE SET steps = EXCLUDED.steps
                        """,
                        (recorded_at, steps)
                    )
        print(f"  Steps intraday: {len(rows)} points")
    except Exception as e:
        print(f"  Steps intraday failed (non-fatal): {e}")


# ── Intraday: Respiration ─────────────────────────────────────────────────────────────────────────
def sync_respiration_intraday(client, conn, day_str):
    try:
        data = client.get_respiration_data(day_str)
        br_list = data.get("respirationValuesArray") or []
        rows = [(ts_from_ms(entry[0]), entry[1]) for entry in br_list if entry[1]]
        if not rows:
            return
        with conn:
            with conn.cursor() as cur:
                for recorded_at, br in rows:
                    cur.execute(
                        """
                        INSERT INTO garmin_respiration_intraday (recorded_at, breathing_rate)
                        VALUES (%s, %s)
                        ON CONFLICT (recorded_at) DO UPDATE SET breathing_rate = EXCLUDED.breathing_rate
                        """,
                        (recorded_at, br)
                    )
        print(f"  Respiration intraday: {len(rows)} points")
    except Exception as e:
        print(f"  Respiration intraday failed (non-fatal): {e}")


# ── Intraday: HRV overnight ──────────────────────────────────────────────────────────────────────
def sync_hrv_intraday(client, conn, day_str):
    try:
        data = client.get_hrv_data(day_str) or {}
        hrv_readings = data.get("hrvReadings") or []
        rows = []
        for entry in hrv_readings:
            if entry.get("hrvValue"):
                ts = parse_gmt_str(entry.get("readingTimeGMT"))
                if ts:
                    rows.append((ts, entry["hrvValue"]))
        if not rows:
            return
        with conn:
            with conn.cursor() as cur:
                for recorded_at, hrv in rows:
                    cur.execute(
                        """
                        INSERT INTO garmin_hrv_intraday (recorded_at, hrv_value)
                        VALUES (%s, %s)
                        ON CONFLICT (recorded_at) DO UPDATE SET hrv_value = EXCLUDED.hrv_value
                        """,
                        (recorded_at, hrv)
                    )
        print(f"  HRV intraday: {len(rows)} readings")
    except Exception as e:
        print(f"  HRV intraday failed (non-fatal): {e}")


# ── Intraday: Sleep ─────────────────────────────────────────────────────────────────────────────
def sync_sleep_intraday(client, conn, day_str):
    """
    Upserts per-epoch sleep data into garmin_sleep_intraday.
    Merges sleep stages, movement, HR, SpO2, respiration, stress, BB, HRV
    by timestamp — each source writes only its column(s).
    """
    try:
        all_sleep_data = client.get_sleep_data(day_str)
        rows = {}  # keyed by recorded_at -> dict of fields

        def add(ts, **kwargs):
            if ts is None:
                return
            if ts not in rows:
                rows[ts] = {}
            for k, v in kwargs.items():
                if v is not None:
                    rows[ts][k] = v

        for entry in (all_sleep_data.get("sleepLevels") or []):
            if entry.get("activityLevel") is not None:
                ts = parse_gmt_str(entry.get("startGMT"))
                duration = None
                if entry.get("startGMT") and entry.get("endGMT"):
                    try:
                        start = datetime.datetime.strptime(entry["startGMT"], "%Y-%m-%dT%H:%M:%S.%f")
                        end = datetime.datetime.strptime(entry["endGMT"], "%Y-%m-%dT%H:%M:%S.%f")
                        duration = int((end - start).total_seconds())
                    except Exception:
                        pass
                add(ts, sleep_stage=entry["activityLevel"], sleep_stage_duration_s=duration)

        for entry in (all_sleep_data.get("sleepMovement") or []):
            ts = parse_gmt_str(entry.get("startGMT"))
            add(ts, movement_level=entry.get("activityLevel"))

        for entry in (all_sleep_data.get("sleepHeartRate") or []):
            ts = ts_from_ms(entry.get("startGMT"))
            add(ts, heart_rate=entry.get("value"))

        for entry in (all_sleep_data.get("wellnessEpochSPO2DataDTOList") or []):
            if entry.get("spo2Reading"):
                ts = parse_gmt_str(entry.get("epochTimestamp"))
                add(ts, spo2=entry["spo2Reading"])

        for entry in (all_sleep_data.get("wellnessEpochRespirationDataDTOList") or []):
            if entry.get("respirationValue"):
                ts = ts_from_ms(entry.get("startTimeGMT"))
                add(ts, respiration_value=entry["respirationValue"])

        for entry in (all_sleep_data.get("sleepStress") or []):
            if entry.get("value"):
                ts = ts_from_ms(entry.get("startGMT"))
                add(ts, stress_value=entry["value"])

        for entry in (all_sleep_data.get("sleepBodyBattery") or []):
            if entry.get("value"):
                ts = ts_from_ms(entry.get("startGMT"))
                add(ts, body_battery=entry["value"])

        for entry in (all_sleep_data.get("hrvData") or []):
            if entry.get("value"):
                ts = ts_from_ms(entry.get("startGMT"))
                add(ts, hrv_value=entry["value"])

        if not rows:
            return

        with conn:
            with conn.cursor() as cur:
                for recorded_at, fields in rows.items():
                    cur.execute(
                        """
                        INSERT INTO garmin_sleep_intraday (
                            recorded_at, sleep_stage, sleep_stage_duration_s,
                            movement_level, heart_rate, spo2, respiration_value,
                            stress_value, body_battery, hrv_value
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (recorded_at) DO UPDATE SET
                            sleep_stage            = COALESCE(EXCLUDED.sleep_stage, garmin_sleep_intraday.sleep_stage),
                            sleep_stage_duration_s = COALESCE(EXCLUDED.sleep_stage_duration_s, garmin_sleep_intraday.sleep_stage_duration_s),
                            movement_level         = COALESCE(EXCLUDED.movement_level, garmin_sleep_intraday.movement_level),
                            heart_rate             = COALESCE(EXCLUDED.heart_rate, garmin_sleep_intraday.heart_rate),
                            spo2                   = COALESCE(EXCLUDED.spo2, garmin_sleep_intraday.spo2),
                            respiration_value      = COALESCE(EXCLUDED.respiration_value, garmin_sleep_intraday.respiration_value),
                            stress_value           = COALESCE(EXCLUDED.stress_value, garmin_sleep_intraday.stress_value),
                            body_battery           = COALESCE(EXCLUDED.body_battery, garmin_sleep_intraday.body_battery),
                            hrv_value              = COALESCE(EXCLUDED.hrv_value, garmin_sleep_intraday.hrv_value)
                        """,
                        (
                            recorded_at,
                            fields.get("sleep_stage"),
                            fields.get("sleep_stage_duration_s"),
                            fields.get("movement_level"),
                            fields.get("heart_rate"),
                            fields.get("spo2"),
                            fields.get("respiration_value"),
                            fields.get("stress_value"),
                            fields.get("body_battery"),
                            fields.get("hrv_value"),
                        )
                    )
        print(f"  Sleep intraday: {len(rows)} epochs")
    except Exception as e:
        print(f"  Sleep intraday failed (non-fatal): {e}")


# ── Activity summary + GPS (FIT) ──────────────────────────────────────────────────────────────────
def sync_activities(client, conn, day_str):
    try:
        activities = client.get_activities_by_date(day_str, day_str) or []
        if not activities:
            return

        for activity in activities:
            activity_id = activity.get("activityId")
            if not activity_id:
                continue

            hr_zone_boundaries = [None] * 5
            try:
                hr_zones_data = client.get_activity_hr_in_timezones(activity_id)
                if hr_zones_data:
                    for zone in hr_zones_data:
                        idx = int(zone.get("zoneNumber", 0)) - 1
                        if 0 <= idx < 5:
                            hr_zone_boundaries[idx] = zone.get("zoneLowBoundary")
            except Exception:
                pass

            start_time = None
            if activity.get("startTimeGMT"):
                try:
                    start_time = datetime.datetime.strptime(
                        activity["startTimeGMT"], "%Y-%m-%d %H:%M:%S"
                    ).replace(tzinfo=datetime.timezone.utc)
                except Exception:
                    pass

            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO garmin_activity_summary (
                            activity_id, start_time, activity_name, activity_type,
                            distance_m, elapsed_duration_s, moving_duration_s,
                            elevation_gain_m, elevation_loss_m,
                            avg_speed, max_speed, calories, bmr_calories,
                            avg_hr, max_hr, vo2max_value, location_name, lap_count,
                            hr_zone_1_s, hr_zone_2_s, hr_zone_3_s, hr_zone_4_s, hr_zone_5_s,
                            hr_zone_low_1, hr_zone_low_2, hr_zone_low_3, hr_zone_low_4, hr_zone_low_5,
                            aerobic_training_effect, anaerobic_training_effect, training_load,
                            intensity_min_moderate, intensity_min_vigorous, updated_at
                        ) VALUES (
                            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, NOW()
                        )
                        ON CONFLICT (activity_id) DO UPDATE SET
                            start_time              = EXCLUDED.start_time,
                            activity_name           = EXCLUDED.activity_name,
                            activity_type           = EXCLUDED.activity_type,
                            distance_m              = EXCLUDED.distance_m,
                            elapsed_duration_s      = EXCLUDED.elapsed_duration_s,
                            moving_duration_s       = EXCLUDED.moving_duration_s,
                            elevation_gain_m        = EXCLUDED.elevation_gain_m,
                            elevation_loss_m        = EXCLUDED.elevation_loss_m,
                            avg_speed               = EXCLUDED.avg_speed,
                            max_speed               = EXCLUDED.max_speed,
                            calories                = EXCLUDED.calories,
                            bmr_calories            = EXCLUDED.bmr_calories,
                            avg_hr                  = EXCLUDED.avg_hr,
                            max_hr                  = EXCLUDED.max_hr,
                            vo2max_value            = EXCLUDED.vo2max_value,
                            location_name           = EXCLUDED.location_name,
                            lap_count               = EXCLUDED.lap_count,
                            hr_zone_1_s             = EXCLUDED.hr_zone_1_s,
                            hr_zone_2_s             = EXCLUDED.hr_zone_2_s,
                            hr_zone_3_s             = EXCLUDED.hr_zone_3_s,
                            hr_zone_4_s             = EXCLUDED.hr_zone_4_s,
                            hr_zone_5_s             = EXCLUDED.hr_zone_5_s,
                            hr_zone_low_1           = EXCLUDED.hr_zone_low_1,
                            hr_zone_low_2           = EXCLUDED.hr_zone_low_2,
                            hr_zone_low_3           = EXCLUDED.hr_zone_low_3,
                            hr_zone_low_4           = EXCLUDED.hr_zone_low_4,
                            hr_zone_low_5           = EXCLUDED.hr_zone_low_5,
                            aerobic_training_effect = EXCLUDED.aerobic_training_effect,
                            anaerobic_training_effect = EXCLUDED.anaerobic_training_effect,
                            training_load           = EXCLUDED.training_load,
                            intensity_min_moderate  = EXCLUDED.intensity_min_moderate,
                            intensity_min_vigorous  = EXCLUDED.intensity_min_vigorous,
                            updated_at              = NOW()
                        """,
                        (
                            activity_id, start_time,
                            activity.get("activityName"),
                            (activity.get("activityType") or {}).get("typeKey"),
                            activity.get("distance"),
                            activity.get("elapsedDuration") or activity.get("duration"),
                            activity.get("movingDuration"),
                            activity.get("elevationGain"),
                            activity.get("elevationLoss"),
                            activity.get("averageSpeed"),
                            activity.get("maxSpeed"),
                            activity.get("calories"),
                            activity.get("bmrCalories"),
                            activity.get("averageHR"),
                            activity.get("maxHR"),
                            activity.get("vO2MaxValue"),
                            activity.get("locationName"),
                            activity.get("lapCount"),
                            activity.get("hrTimeInZone_1"),
                            activity.get("hrTimeInZone_2"),
                            activity.get("hrTimeInZone_3"),
                            activity.get("hrTimeInZone_4"),
                            activity.get("hrTimeInZone_5"),
                            hr_zone_boundaries[0],
                            hr_zone_boundaries[1],
                            hr_zone_boundaries[2],
                            hr_zone_boundaries[3],
                            hr_zone_boundaries[4],
                            activity.get("aerobicTrainingEffect"),
                            activity.get("anaerobicTrainingEffect"),
                            activity.get("activityTrainingLoad"),
                            activity.get("moderateIntensityMinutes"),
                            activity.get("vigorousIntensityMinutes"),
                        )
                    )
            print(f"  Activity {activity_id} ({(activity.get('activityType') or {}).get('typeKey', '?')}) upserted")

            if activity.get("hasPolyline") and HAS_FITPARSE:
                sync_activity_gps(client, conn, activity_id)

        print(f"  Activities synced for {day_str}: {len(activities)}")
    except Exception as e:
        print(f"  Activity sync failed (non-fatal): {e}")


def sync_activity_gps(client, conn, activity_id):
    """Download FIT file and upsert per-second GPS trackpoints."""
    try:
        zip_data = client.download_activity(activity_id, dl_fmt=client.ActivityDownloadFormat.ORIGINAL)
        zip_buffer = io.BytesIO(zip_data)
        with zipfile.ZipFile(zip_buffer) as zf:
            fit_filename = next((f for f in zf.namelist() if f.endswith(".fit")), None)
            if not fit_filename:
                print(f"    No FIT file in zip for activity {activity_id}, skipping GPS")
                return
            fit_data = zf.read(fit_filename)

        fitfile = FitFile(io.BytesIO(fit_data))
        fitfile.parse()
        records = [r.get_values() for r in fitfile.get_messages("record")]
        if not records:
            print(f"    No records in FIT for activity {activity_id}")
            return

        activity_start = records[0]["timestamp"].replace(tzinfo=datetime.timezone.utc)
        gps_rows = []
        for r in records:
            if not r.get("timestamp"):
                continue
            ts = r["timestamp"].replace(tzinfo=datetime.timezone.utc)
            lat = int(r["position_lat"]) * (180 / 2**31) if r.get("position_lat") else None
            lon = int(r["position_long"]) * (180 / 2**31) if r.get("position_long") else None
            duration_s = (ts - activity_start).total_seconds()
            alt = r.get("enhanced_altitude") or r.get("altitude")
            speed = r.get("enhanced_speed") or r.get("speed")
            gps_rows.append((
                activity_id, ts, duration_s, lat, lon, alt,
                r.get("distance"), speed,
                float(r["heart_rate"]) if r.get("heart_rate") else None,
                r.get("cadence"),
                r.get("power"),
                r.get("temperature"),
            ))

        with conn:
            with conn.cursor() as cur:
                for row in gps_rows:
                    cur.execute(
                        """
                        INSERT INTO garmin_activity_gps (
                            activity_id, recorded_at, duration_s, lat, lon, altitude_m,
                            distance_m, speed_mps, heart_rate, cadence, power_w, temperature_c
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (activity_id, recorded_at) DO NOTHING
                        """,
                        row
                    )
        print(f"    GPS: {len(gps_rows)} trackpoints for activity {activity_id}")
    except Exception as e:
        print(f"    GPS sync failed for activity {activity_id} (non-fatal): {e}")


# ── Race predictions ──────────────────────────────────────────────────────────────────────────────
def sync_race_predictions(client, conn, day_str):
    try:
        rp_list = client.get_race_predictions(startdate=day_str, enddate=day_str, _type="daily")
        if not rp_list:
            return
        rp = rp_list[0]
        fields = {
            "time_5k_s":            rp.get("time5K"),
            "time_10k_s":           rp.get("time10K"),
            "time_half_marathon_s": rp.get("timeHalfMarathon"),
            "time_marathon_s":      rp.get("timeMarathon"),
        }
        if all(v is None for v in fields.values()):
            return
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO garmin_race_predictions
                        (date, time_5k_s, time_10k_s, time_half_marathon_s, time_marathon_s, updated_at)
                    VALUES (%s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (date) DO UPDATE SET
                        time_5k_s            = EXCLUDED.time_5k_s,
                        time_10k_s           = EXCLUDED.time_10k_s,
                        time_half_marathon_s = EXCLUDED.time_half_marathon_s,
                        time_marathon_s      = EXCLUDED.time_marathon_s,
                        updated_at           = NOW()
                    """,
                    (day_str, fields["time_5k_s"], fields["time_10k_s"],
                     fields["time_half_marathon_s"], fields["time_marathon_s"])
                )
        print(f"  Race predictions upserted for {day_str}")
    except Exception as e:
        print(f"  Race predictions failed (non-fatal): {e}")


# ── Daily garmin_daily row ──────────────────────────────────────────────────────────────────────────────
def sync_date(client, target_date, conn, existing_cols):
    day_str = target_date.isoformat()
    print(f"\n{'='*50}")
    print(f"Syncing {day_str}...")
    print(f"{'='*50}")

    steps = None
    calories = None
    resting_hr = None

    sleep_seconds = None
    sleep_score = None
    sleep_notes = None
    deep_sleep_seconds = None
    light_sleep_seconds = None
    rem_sleep_seconds = None
    awake_seconds = None
    sleep_stress_avg = None

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

    # New fields
    active_kilocalories = None
    bmr_kilocalories = None
    active_seconds = None
    highly_active_seconds = None
    sedentary_seconds = None
    sleeping_seconds = None
    distance_meters = None
    max_hr = None
    min_hr = None
    body_battery_charged = None
    body_battery_drained = None
    body_battery_high = None
    body_battery_low = None
    body_battery_at_wake = None
    body_battery_during_sleep = None
    stress_duration_high = None
    stress_duration_medium = None
    stress_duration_low = None
    stress_duration_rest = None
    stress_duration_uncategorized = None
    avg_skin_temp_deviation_c = None
    floors_ascended = None
    floors_descended = None
    floors_ascended_m = None
    floors_descended_m = None

    stats = client.get_stats(day_str)
    dump_payload("stats", stats)

    steps = stats.get("totalSteps")
    calories = stats.get("totalKilocalories")
    resting_hr = stats.get("restingHeartRate")
    active_kilocalories = stats.get("activeKilocalories")
    bmr_kilocalories = stats.get("bmrKilocalories")
    active_seconds = stats.get("activeSeconds")
    highly_active_seconds = stats.get("highlyActiveSeconds")
    sedentary_seconds = stats.get("sedentarySeconds")
    sleeping_seconds = stats.get("sleepingSeconds")
    distance_meters = stats.get("totalDistanceMeters")
    max_hr = stats.get("maxHeartRate")
    min_hr = stats.get("minHeartRate")
    body_battery_charged = stats.get("bodyBatteryChargedValue")
    body_battery_drained = stats.get("bodyBatteryDrainedValue")
    body_battery_high = stats.get("bodyBatteryHighestValue")
    body_battery_low = stats.get("bodyBatteryLowestValue")
    body_battery_at_wake = stats.get("bodyBatteryAtWakeTime")
    body_battery_during_sleep = stats.get("bodyBatteryDuringSleep")
    stress_duration_high = stats.get("highStressDuration")
    stress_duration_medium = stats.get("mediumStressDuration")
    stress_duration_low = stats.get("lowStressDuration")
    stress_duration_rest = stats.get("restStressDuration")
    stress_duration_uncategorized = stats.get("uncategorizedStressDuration")
    avg_skin_temp_deviation_c = stats.get("avgSkinTempDeviationC")
    floors_ascended = stats.get("floorsAscended")
    floors_descended = stats.get("floorsDescended")
    floors_ascended_m = stats.get("floorsAscendedInMeters")
    floors_descended_m = stats.get("floorsDescendedInMeters")

    try:
        sleep_data = client.get_sleep_data(day_str)
        dump_payload("sleep_data", sleep_data)
        daily_sleep = sleep_data.get("dailySleepDTO", {})
        sleep_seconds = daily_sleep.get("sleepTimeSeconds")
        sleep_notes = daily_sleep.get("userNote") or None
        sleep_stress_avg = daily_sleep.get("avgSleepStress")
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

    try:
        hrv_data = client.get_hrv_data(day_str)
        dump_payload("hrv_data", hrv_data)
        hrv_summary = hrv_data.get("hrvSummary", {})
        hrv = hrv_summary.get("lastNightAvg")
        if hrv is None:
            readings = hrv_data.get("hrvReadings", [])
            if readings:
                hrv_values = [r.get("hrvValue") for r in readings if r.get("hrvValue") is not None]
                hrv = round(sum(hrv_values) / len(hrv_values), 2) if hrv_values else None
    except Exception as e:
        print(f"HRV fetch failed: {e}")

    try:
        readiness_data = client.get_training_readiness(day_str)
        dump_payload("training_readiness", readiness_data)
        if isinstance(readiness_data, list) and readiness_data:
            training_readiness = readiness_data[-1].get("score")
        elif isinstance(readiness_data, dict):
            training_readiness = readiness_data.get("score")
    except Exception as e:
        print(f"Training readiness fetch failed: {e}")

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

    try:
        stress_data = client.get_stress_data(day_str)
        dump_payload("stress_data", stress_data)
        stress_avg = stress_data.get("avgStressLevel")
        stress_max = stress_data.get("maxStressLevel")
    except Exception as e:
        print(f"Stress fetch failed: {e}")

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
                or (str(latest_status.get("trainingStatus")) if latest_status.get("trainingStatus") is not None else None)
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

    recovery_time_data = (
        call_if_exists(client, "get_recovery_time", day_str)
        or call_if_exists(client, "get_recovery_time_data", day_str)
    )
    if recovery_time_data is not None:
        dump_payload("recovery_time_data", recovery_time_data)
        raw_recovery = first_non_null(
            recursive_find_first(recovery_time_data, {
                "recoveryTime", "recoveryTimeHrs", "recoveryHours",
                "recoveryTimeHours", "recoveryTimeInSeconds",
                "recoveryTimeSeconds", "recoveryTimeMillis", "remainingRecoveryTime"
            })
        )
        recovery_time_hours = normalize_recovery_time_hours(raw_recovery)

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

    row_data = {
        "date": target_date,
        "steps": steps,
        "calories": calories,
        "resting_hr": resting_hr,
        "sleep_seconds": sleep_seconds,
        "sleep_notes": sleep_notes,
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
        # New columns
        "active_kilocalories": active_kilocalories,
        "bmr_kilocalories": bmr_kilocalories,
        "active_seconds": active_seconds,
        "highly_active_seconds": highly_active_seconds,
        "sedentary_seconds": sedentary_seconds,
        "sleeping_seconds": sleeping_seconds,
        "distance_meters": distance_meters,
        "max_hr": max_hr,
        "min_hr": min_hr,
        "body_battery_charged": body_battery_charged,
        "body_battery_drained": body_battery_drained,
        "body_battery_high": body_battery_high,
        "body_battery_low": body_battery_low,
        "body_battery_at_wake": body_battery_at_wake,
        "body_battery_during_sleep": body_battery_during_sleep,
        "stress_duration_high": stress_duration_high,
        "stress_duration_medium": stress_duration_medium,
        "stress_duration_low": stress_duration_low,
        "stress_duration_rest": stress_duration_rest,
        "stress_duration_uncategorized": stress_duration_uncategorized,
        "sleep_stress_avg": sleep_stress_avg,
        "avg_skin_temp_deviation_c": avg_skin_temp_deviation_c,
        "floors_ascended": floors_ascended,
        "floors_descended": floors_descended,
        "floors_ascended_m": floors_ascended_m,
        "floors_descended_m": floors_descended_m,
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

    # Intraday syncs for this date
    sync_hr_intraday(client, conn, day_str)
    sync_stress_bb_intraday(client, conn, day_str)
    sync_steps_intraday(client, conn, day_str)
    sync_respiration_intraday(client, conn, day_str)
    sync_hrv_intraday(client, conn, day_str)
    sync_sleep_intraday(client, conn, day_str)
    sync_activities(client, conn, day_str)
    sync_race_predictions(client, conn, day_str)


def main():
    today = datetime.date.today()

    print(f"Using token directory: {TOKEN_DIR}")
    print(f"Syncing last {LOOKBACK_DAYS} days (today + {LOOKBACK_DAYS - 1} prior)...")

    try:
        validate_token_dir()

        client = Garmin()
        client.login(tokenstore=TOKEN_DIR)

        validate_garmin_session(client)

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

        sync_ftp(client, conn)

        # Persist refreshed tokens back to disk so they don't expire prematurely
        try:
            client.garth.dump(TOKEN_DIR)
            print(f"Tokens saved to {TOKEN_DIR}")
        except Exception as e:
            print(f"WARNING: Failed to save tokens: {e}", file=sys.stderr)

        conn.close()
        print("\nAll dates synced.")

    except Exception as e:
        print(f"Sync failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
    sys.exit(0)
