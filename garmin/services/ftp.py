from __future__ import annotations

import datetime

from utils import deep_get, dump_payload


def sync_ftp(client, conn) -> None:
    print("\nSyncing cycling FTP...")
    try:
        data = client.get_cycling_ftp()
        dump_payload("cycling_ftp", data)

        ftp_watts = data.get("functionalThresholdPower")
        sport = data.get("sport", "CYCLING")
        source = data.get("biometricSourceType")
        calendar_dt = data.get("calendarDate")

        if ftp_watts is None:
            print("  No FTP value returned, skipping.")
            return

        ftp_date = (
            datetime.date.fromisoformat(str(calendar_dt)[:10])
            if calendar_dt
            else datetime.date.today()
        )

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
                    print(f"  W/kg: {ftp_watts}W / {row[0]}kg = {power_to_weight}")
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
                    (ftp_date, ftp_watts, power_to_weight, threshold_hr_cycling, sport, source),
                )
        print(f"  FTP upserted: {ftp_watts}W on {ftp_date} (W/kg: {power_to_weight})")
    except Exception as e:
        print(f"  FTP sync failed (non-fatal): {e}")
