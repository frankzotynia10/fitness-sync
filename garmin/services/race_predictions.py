from __future__ import annotations


def sync_race_predictions(client, conn, day_str: str) -> None:
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
                    (
                        day_str,
                        fields["time_5k_s"],
                        fields["time_10k_s"],
                        fields["time_half_marathon_s"],
                        fields["time_marathon_s"],
                    ),
                )
        print(f"  Race predictions upserted for {day_str}")
    except Exception as e:
        print(f"  Race predictions failed (non-fatal): {e}")
