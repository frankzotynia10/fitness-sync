"""Pull the same days/activities from garminconnect (unofficial) and the Personal API.

Runs on Andromeda inside the garmin-sync image, with the garminconnect token dir and
the garmin .env mounted (see FINDINGS.md). Writes raw payloads to ./cmp/{gc,official}/.
Read-only on both sides; never saves garminconnect tokens.

    python compare_pull.py --days 7
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, "/app")  # garmin-sync code inside the image

import probe  # noqa: E402

OUT = HERE / "cmp"
CYCLING = {"cycling", "road_biking", "virtual_ride", "indoor_cycling", "gravel_cycling", "mountain_biking"}


def save(side: str, name: str, data) -> None:
    d = OUT / side
    d.mkdir(parents=True, exist_ok=True)
    if isinstance(data, (bytes, bytearray)):
        (d / name).write_bytes(data)
    else:
        (d / f"{name}.json").write_text(json.dumps(data, default=str, indent=1), encoding="utf-8")


def gc_call(client, label, method, *args, **kw):
    fn = getattr(client, method, None)
    if fn is None:
        print(f"  gc  {label}: no method {method}")
        return None
    try:
        data = fn(*args, **kw)
        save("gc", label, data)
        return data
    except Exception as e:  # noqa: BLE001
        print(f"  gc  {label}: {type(e).__name__}: {str(e)[:120]}")
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()
    end = dt.date.today()
    start = end - dt.timedelta(days=args.days - 1)
    days = [start + dt.timedelta(days=i) for i in range(args.days)]
    s, e = start.isoformat(), end.isoformat()

    # ── garminconnect ────────────────────────────────────────
    from services.client import build_client
    gc = build_client()
    for d in days:
        ds = d.isoformat()
        for label, method, a in [
            ("stats", "get_stats", (ds,)), ("sleep", "get_sleep_data", (ds,)),
            ("hrv", "get_hrv_data", (ds,)), ("readiness", "get_training_readiness", (ds,)),
            ("max_metrics", "get_max_metrics", (ds,)), ("stress", "get_stress_data", (ds,)),
            ("training_status", "get_training_status", (ds,)),
            ("weigh_ins", "get_daily_weigh_ins", (ds,)), ("stats_body", "get_stats_and_body", (ds,)),
            ("heart_rates", "get_heart_rates", (ds,)), ("steps", "get_steps_data", (ds,)),
            ("respiration", "get_respiration_data", (ds,)), ("spo2", "get_spo2_data", (ds,)),
            ("fitness_age", "get_fitnessage_data", (ds,)), ("hydration", "get_hydration_data", (ds,)),
            ("body_battery", "get_body_battery", (ds, ds)),
        ]:
            gc_call(gc, f"{label}_{ds}", method, *a)
    gc_call(gc, "endurance", "get_endurance_score", s, e)
    gc_call(gc, "race_predictions", "get_race_predictions")
    gc_call(gc, "ftp", "get_cycling_ftp")
    gc_call(gc, "lactate_threshold", "get_lactate_threshold")
    gc_call(gc, "hill_score", "get_hill_score", s, e)

    acts = gc_call(gc, "activities_range", "get_activities_by_date", s, e) or []
    # make sure one cycling ride with power is in the set
    recent = gc_call(gc, "activities_60d", "get_activities_by_date",
                     (end - dt.timedelta(days=60)).isoformat(), e) or []
    ride = next((a for a in recent if (a.get("activityType") or {}).get("typeKey") in CYCLING), None)
    act_ids = [a["activityId"] for a in acts][:4]
    if ride and ride["activityId"] not in act_ids:
        act_ids.append(ride["activityId"])
    print(f"activities: {act_ids} (ride: {ride and ride['activityId']})")
    for aid in act_ids:
        gc_call(gc, f"activity_{aid}", "get_activity", aid)
        gc_call(gc, f"activity_hrzones_{aid}", "get_activity_hr_in_timezones", aid)
        gc_call(gc, f"activity_details_{aid}", "get_activity_details", aid)
        gc_call(gc, f"activity_fit_{aid}.zip", "download_activity", aid,
                dl_fmt=gc.ActivityDownloadFormat.ORIGINAL)

    # ── Personal API ─────────────────────────────────────────
    tok = probe.get_token(probe.load_key())
    auth = {"Authorization": f"Bearer {tok}"}

    def off(label, path, binary=False):
        st, _, body = probe.http("GET", probe.HOST + path, {**auth, **({"Accept": "*/*"} if binary else {})})
        if st != 200:
            print(f"  off {label}: HTTP {st} {body[:100]!r}")
            return
        save("official", label + (".zip" if binary else ""),
             body if binary else json.loads(body or b"null"))

    long_start = (end - dt.timedelta(days=60)).isoformat()
    for label, path in [
        ("daily_summaries", f"/usersummary-service/v2/dailysummaries?startDate={s}&endDate={e}"),
        ("stress", f"/usersummary-service/stats/stress/daily/{s}/{e}"),
        ("steps", f"/usersummary-service/stats/steps/daily/{s}/{e}"),
        ("hydration", f"/usersummary-service/stats/hydration/daily/{s}/{e}"),
        ("weight_goals", f"/goal-service/goal/user/effective/weightgoal/{s}/{e}"),
        ("weight", f"/weight-service/weight/range/{s}/{e}?includeAll=true"),
        ("profile", "/userprofile-service/userprofile/settings"),
        ("sleep_summaries", f"/sleep-service/sleep/dailySleepSummariesByDate?startDate={s}&endDate={e}"),
        ("body_battery", f"/wellness-service/wellness/bodyBattery/reports/daily?startDate={s}&endDate={e}"),
        ("hrv_summaries", f"/hrv-service/hrv/daily/{s}/{e}"),
        ("vo2max_60d", f"/metrics-service/metrics/maxmet/daily/{long_start}/{e}"),
        ("hr_zones", "/biometric-service/heartRateZones/v2"),
        ("wellness_activities", f"/wellnessactivity-service/activity/summary/range/{s}/{e}"),
        ("activity_types", "/activity-service/activity/activityTypes/simple"),
        ("activities_range", f"/activitylist-service/activities/search/activities?start=0&limit=50&startDate={s}&endDate={e}"),
    ]:
        off(label, path)
    for d in days:
        ds = d.isoformat()
        off(f"sleep_{ds}", f"/sleep-service/sleep/dailySleep?date={ds}")
        off(f"hrv_{ds}", f"/hrv-service/hrv/{ds}")
        off(f"heart_rate_{ds}", f"/wellness-service/wellness/dailyHeartRate?date={ds}")
        off(f"spo2_{ds}", f"/wellness-service/wellness/daily/spo2acclimation/{ds}")
        off(f"fitness_age_{ds}", f"/fitnessage-service/fitnessage/v2/{ds}")
        off(f"food_{ds}", f"/nutrition-service/food/logs/{ds}")
    for aid in act_ids:
        off(f"activity_{aid}", f"/activity-service/activity/{aid}")
        off(f"activity_workouts_{aid}", f"/activity-service/activity/{aid}/workouts")
        off(f"activity_fit_{aid}", f"/download-service/files/activity/{aid}", binary=True)
    print(f"done -> {OUT}")


if __name__ == "__main__":
    main()
