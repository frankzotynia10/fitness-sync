from __future__ import annotations

from utils import ts_from_ms, parse_gmt_str, dump_payload


def sync_hr_intraday(client, conn, day_str: str) -> None:
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
                        (recorded_at, hr),
                    )
        print(f"  HR intraday: {len(rows)} points")
    except Exception as e:
        print(f"  HR intraday failed (non-fatal): {e}")


def sync_stress_bb_intraday(client, conn, day_str: str) -> None:
    try:
        data = client.get_stress_data(day_str)
        stress_rows = [
            (ts_from_ms(entry[0]), entry[1])
            for entry in (data.get("stressValuesArray") or [])
            if entry[1] is not None and entry[1] >= 0
        ]
        bb_rows = [
            (ts_from_ms(entry[0]), entry[2])
            for entry in (data.get("bodyBatteryValuesArray") or [])
            if len(entry) >= 3 and entry[2] is not None
        ]
        with conn:
            with conn.cursor() as cur:
                for recorded_at, stress in stress_rows:
                    cur.execute(
                        """
                        INSERT INTO garmin_stress_intraday (recorded_at, stress_level)
                        VALUES (%s, %s)
                        ON CONFLICT (recorded_at) DO UPDATE SET stress_level = EXCLUDED.stress_level
                        """,
                        (recorded_at, stress),
                    )
                for recorded_at, bb in bb_rows:
                    cur.execute(
                        """
                        INSERT INTO garmin_bb_intraday (recorded_at, body_battery)
                        VALUES (%s, %s)
                        ON CONFLICT (recorded_at) DO UPDATE SET body_battery = EXCLUDED.body_battery
                        """,
                        (recorded_at, bb),
                    )
        print(f"  Stress intraday: {len(stress_rows)} points, BB intraday: {len(bb_rows)} points")
    except Exception as e:
        print(f"  Stress/BB intraday failed (non-fatal): {e}")


def sync_steps_intraday(client, conn, day_str: str) -> None:
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
                        (recorded_at, steps),
                    )
        print(f"  Steps intraday: {len(rows)} points")
    except Exception as e:
        print(f"  Steps intraday failed (non-fatal): {e}")


def sync_respiration_intraday(client, conn, day_str: str) -> None:
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
                        (recorded_at, br),
                    )
        print(f"  Respiration intraday: {len(rows)} points")
    except Exception as e:
        print(f"  Respiration intraday failed (non-fatal): {e}")


def sync_hrv_intraday(client, conn, day_str: str) -> None:
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
                        (recorded_at, hrv),
                    )
        print(f"  HRV intraday: {len(rows)} readings")
    except Exception as e:
        print(f"  HRV intraday failed (non-fatal): {e}")


def sync_sleep_intraday(client, conn, day_str: str) -> None:
    """
    Upserts per-epoch sleep data into garmin_sleep_intraday.
    Merges sleep stages, movement, HR, SpO2, respiration, stress, BB, HRV
    by timestamp.
    """
    try:
        all_sleep_data = client.get_sleep_data(day_str)
        rows: dict = {}

        def add(ts, **kwargs):
            if ts is None:
                return
            if ts not in rows:
                rows[ts] = {}
            for k, v in kwargs.items():
                if v is not None:
                    rows[ts][k] = v

        import datetime
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
            add(parse_gmt_str(entry.get("startGMT")), movement_level=entry.get("activityLevel"))

        for entry in (all_sleep_data.get("sleepHeartRate") or []):
            add(ts_from_ms(entry.get("startGMT")), heart_rate=entry.get("value"))

        for entry in (all_sleep_data.get("wellnessEpochSPO2DataDTOList") or []):
            if entry.get("spo2Reading"):
                add(parse_gmt_str(entry.get("epochTimestamp")), spo2=entry["spo2Reading"])

        for entry in (all_sleep_data.get("wellnessEpochRespirationDataDTOList") or []):
            if entry.get("respirationValue"):
                add(ts_from_ms(entry.get("startTimeGMT")), respiration_value=entry["respirationValue"])

        for entry in (all_sleep_data.get("sleepStress") or []):
            if entry.get("value"):
                add(ts_from_ms(entry.get("startGMT")), stress_value=entry["value"])

        for entry in (all_sleep_data.get("sleepBodyBattery") or []):
            if entry.get("value"):
                add(ts_from_ms(entry.get("startGMT")), body_battery=entry["value"])

        for entry in (all_sleep_data.get("hrvData") or []):
            if entry.get("value"):
                add(ts_from_ms(entry.get("startGMT")), hrv_value=entry["value"])

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
                        ),
                    )
        print(f"  Sleep intraday: {len(rows)} epochs")
    except Exception as e:
        print(f"  Sleep intraday failed (non-fatal): {e}")
