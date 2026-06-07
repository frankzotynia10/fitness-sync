from __future__ import annotations

import datetime
import io
import urllib.request
import zipfile

try:
    from fitparse import FitFile
    HAS_FITPARSE = True
except ImportError:
    HAS_FITPARSE = False
    print("WARNING: fitparse not installed — GPS/FIT sync will be skipped.")

# n8n webhook URLs
WEBHOOK_STRENGTH = "https://n8n.mayfairlabs.cloud/webhook/garmin-activity-strength"
WEBHOOK_CARDIO   = "https://n8n.mayfairlabs.cloud/webhook/garmin-activity-cardio"

STRENGTH_TYPES = {"strength_training", "weight_training"}
CARDIO_TYPES   = {"road_biking", "cycling", "indoor_cycling", "virtual_ride", "walking", "running"}


def _fire_webhook(url: str, activity_id: int, activity_type: str) -> None:
    try:
        payload = f'{{"activity_id": {activity_id}, "activity_type": "{activity_type}"}}'.encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10):
            pass
        print(f"  Webhook fired: {url} ({activity_type})")
    except Exception as e:
        print(f"  Webhook failed (non-fatal): {url} — {e}")


def sync_activities(client, conn, day_str: str) -> None:
    try:
        activities = client.get_activities_by_date(day_str, day_str) or []
        if not activities:
            return

        for activity in activities:
            activity_id = activity.get("activityId")
            if not activity_id:
                continue

            activity_type = (activity.get("activityType") or {}).get("typeKey")

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
                            aerobic_training_effect   = EXCLUDED.aerobic_training_effect,
                            anaerobic_training_effect = EXCLUDED.anaerobic_training_effect,
                            training_load           = EXCLUDED.training_load,
                            intensity_min_moderate  = EXCLUDED.intensity_min_moderate,
                            intensity_min_vigorous  = EXCLUDED.intensity_min_vigorous,
                            updated_at              = NOW()
                        """,
                        (
                            activity_id, start_time,
                            activity.get("activityName"),
                            activity_type,
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
                        ),
                    )
            print(f"  Activity {activity_id} ({activity_type}) upserted")

            # Fire appropriate webhook based on activity type
            if activity_type in STRENGTH_TYPES:
                _fire_webhook(WEBHOOK_STRENGTH, activity_id, activity_type)
            elif activity_type in CARDIO_TYPES:
                _fire_webhook(WEBHOOK_CARDIO, activity_id, activity_type)
            else:
                print(f"  Activity type '{activity_type}' — no webhook triggered")

            # Always download FIT for strength activities (HR data) and
            # for any activity with a polyline (GPS data)
            if HAS_FITPARSE and (activity_type in STRENGTH_TYPES or activity.get("hasPolyline")):
                _sync_activity_gps(client, conn, activity_id)

        print(f"  Activities synced for {day_str}: {len(activities)}")
    except Exception as e:
        print(f"  Activity sync failed (non-fatal): {e}")


def _sync_activity_gps(client, conn, activity_id: int) -> None:
    """Download FIT file and upsert per-second HR + GPS trackpoints."""
    try:
        zip_data = client.download_activity(activity_id, dl_fmt=client.ActivityDownloadFormat.ORIGINAL)
        zip_buffer = io.BytesIO(zip_data)
        with zipfile.ZipFile(zip_buffer) as zf:
            fit_filename = next((f for f in zf.namelist() if f.endswith(".fit")), None)
            if not fit_filename:
                print(f"    No FIT file in zip for activity {activity_id}, skipping")
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
                        row,
                    )
        print(f"    FIT: {len(gps_rows)} records for activity {activity_id}")
    except Exception as e:
        print(f"    FIT sync failed for activity {activity_id} (non-fatal): {e}")
