import json, glob, os, sys, hashlib, zipfile, io
B = sys.argv[1]
def J(side, name):
    p = f"{B}/{side}/{name}.json"
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None
def g(o, path):
    for k in path.split("."):
        if o is None: return None
        o = (o[int(k)] if int(k) < len(o) else None) if isinstance(o, list) else o.get(k)
    return o
ds_all = J("official", "daily_summaries")["data"]; off_ds = {d["calendarDate"]: d for d in ds_all}
stress_off = {d["calendarDate"]: d["values"] for d in J("official", "stress")}
w_off = {d["summaryDate"]: d for d in J("official", "weight")["dailyWeightSummaries"]}
days = sorted(off_ds)
MAP = [  # label, gc file, gc path, official getter, scale
 ("steps","stats","totalSteps", lambda d: g(off_ds[d],"movement.steps.value"),1),
 ("distance_m","stats","totalDistanceMeters", lambda d: g(off_ds[d],"movement.steps.distanceInMeters"),1),
 ("floors_up","stats","floorsAscended", lambda d: g(off_ds[d],"movement.floorsAscended.value"),1),
 ("kcal_total","stats","totalKilocalories", lambda d: g(off_ds[d],"calories.burnedTotal"),1),
 ("kcal_active","stats","activeKilocalories", lambda d: g(off_ds[d],"calories.burnedActive"),1),
 ("kcal_bmr","stats","bmrKilocalories", lambda d: g(off_ds[d],"calories.burnedResting"),1),
 ("rhr","stats","restingHeartRate", lambda d: g(off_ds[d],"heartRate.restingValue"),1),
 ("hr_min","stats","minHeartRate", lambda d: g(off_ds[d],"heartRate.minValue"),1),
 ("hr_max","stats","maxHeartRate", lambda d: g(off_ds[d],"heartRate.maxValue"),1),
 ("hr_max(dailyHeartRate ep)","stats","maxHeartRate", lambda d: g(J("official",f"heart_rate_{d}"),"maxHeartRate"),1),
 ("hr_min(dailyHeartRate ep)","stats","minHeartRate", lambda d: g(J("official",f"heart_rate_{d}"),"minHeartRate"),1),
 ("gc hr_max(heart_rates ep)","heart_rates","maxHeartRate", lambda d: g(off_ds[d],"heartRate.maxValue"),1),
 ("stress_avg","stress","avgStressLevel", lambda d: g(off_ds[d],"stress.avgLevel"),1),
 ("stress_max","stress","maxStressLevel", lambda d: g(off_ds[d],"stress.maxLevel"),1),
 ("stress_high_s","stats","highStressDuration", lambda d: (g(off_ds[d],"stress.highStressDurationInMillis") or 0)/1000,1),
 ("stress_rest_s","stats","restStressDuration", lambda d: (g(off_ds[d],"stress.restDurationInMillis") or 0)/1000,1),
 ("stress_high_s(stats/stress ep)","stats","highStressDuration", lambda d: g(stress_off.get(d),"highStressDuration"),1),
 ("bb_charged","stats","bodyBatteryChargedValue", lambda d: g(off_ds[d],"bodyBattery.chargedValue"),1),
 ("bb_drained","stats","bodyBatteryDrainedValue", lambda d: g(off_ds[d],"bodyBattery.drainedValue"),1),
 ("bb_high","stats","bodyBatteryHighestValue", lambda d: g(off_ds[d],"bodyBattery.maxValue"),1),
 ("bb_low","stats","bodyBatteryLowestValue", lambda d: g(off_ds[d],"bodyBattery.minValue"),1),
 ("intensity_mod","stats_body","moderateIntensityMinutes", lambda d: g(off_ds[d],"intensityMinutes.moderate"),1),
 ("intensity_vig","stats_body","vigorousIntensityMinutes", lambda d: g(off_ds[d],"intensityMinutes.vigorous"),1),
 ("resp_avg_waking","stats_body","avgWakingRespirationValue", lambda d: g(off_ds[d],"respiration.avgValue"),1),
 ("spo2_avg","stats_body","averageSpo2", lambda d: g(off_ds[d],"pulseOx.avgValue"),1),
 ("spo2_min","stats_body","lowestSpo2", lambda d: g(off_ds[d],"pulseOx.minValue"),1),
 ("sleep_s","sleep","dailySleepDTO.sleepTimeSeconds", lambda d: g(J("official",f"sleep_{d}"),"sleepTimeSeconds"),1),
 ("deep_s","sleep","dailySleepDTO.deepSleepSeconds", lambda d: g(J("official",f"sleep_{d}"),"deepSleepSeconds"),1),
 ("rem_s","sleep","dailySleepDTO.remSleepSeconds", lambda d: g(J("official",f"sleep_{d}"),"remSleepSeconds"),1),
 ("sleep_score","sleep","dailySleepDTO.sleepScores.overall.value", lambda d: g(J("official",f"sleep_{d}"),"sleepScores.overall.value"),1),
 ("sleep_stress","sleep","dailySleepDTO.avgSleepStress", lambda d: g(J("official",f"sleep_{d}"),"avgSleepStress"),1),
 ("hrv_last_night","hrv","hrvSummary.lastNightAvg", lambda d: g(J("official",f"hrv_{d}"),"hrvSummary.lastNightAvg"),1),
 ("weight_g","weigh_ins","dateWeightList.0.weight", lambda d: g(w_off.get(d),"latestWeight.weight"),1),
]
tot = same = 0; diffs = []
print(f"{'field':32}" + "".join(f"{d[5:]:>16}" for d in days))
for label, gf, gp, og, _ in MAP:
    row = f"{label:32}"
    for d in days:
        a = g(J("gc", f"{gf}_{d}"), gp); b = og(d) if d in off_ds else None
        if a is None and b is None: row += f"{'·':>16}"; continue
        tot += 1
        eq = a is not None and b is not None and abs(float(a) - float(b)) < 0.51
        same += eq
        if not eq: diffs.append((label, d, a, b))
        row += f"{'=' if eq else f'{a}|{b}':>16}"
    print(row)
print(f"\n{same}/{tot} daily values identical")
for x in diffs: print("DIFF", x)
# intraday series
for name, gf, gk, of, ok in [("HR","heart_rates","heartRateValues","heart_rate","heartRateValues"),
                             ("HRV","hrv","hrvReadings","hrv","hrvReadings")]:
    for d in days:
        a = g(J("gc", f"{gf}_{d}"), gk) or []; b = g(J("official", f"{of}_{d}"), ok) or []
        print(f"{name} {d}: gc {len(a)} pts, official {len(b)} pts, identical={a==b}")
bb_gc = {d: g(J("gc", f"body_battery_{d}"), "0.bodyBatteryValuesArray") for d in days}
bb_off = {x["date"]: x.get("bodyBatteryValuesArray") for x in J("official", "body_battery")}
print("BB intraday identical per day:", {d[5:]: bb_gc[d] == bb_off.get(d) for d in days})
# activities
gl = {a["activityId"]: a for a in J("gc", "activities_60d")}; ol = {a["activityId"]: a for a in J("official", "activities_range")}
for f in sorted(glob.glob(f"{B}/official/activity_[0-9]*.json")):
    aid = int(os.path.basename(f)[9:-5]); o = json.load(open(f, encoding="utf-8")); gcd = J("gc", f"activity_{aid}")
    os_, gs = o.get("summaryDTO", {}), (gcd or {}).get("summaryDTO", {})
    only_gc = sorted(set(gs) - set(os_)); only_off = sorted(set(os_) - set(gs))
    vd = [k for k in set(gs) & set(os_) if gs[k] != os_[k]]
    t = (gcd or {}).get("activityTypeDTO", {}).get("typeKey")
    print(f"\nactivity {aid} ({t}): summaryDTO {len(gs)} gc keys / {len(os_)} official; only-gc {only_gc}; only-official {only_off}; value diffs {vd}")
    tl = (gcd or {}); ol_keys = set(o) ; gc_keys = set(tl)
    print(f"   top-level only-gc {sorted(gc_keys-ol_keys)}; only-official {sorted(ol_keys-gc_keys)}")
    if aid in gl and aid in ol:
        a, b = gl[aid], ol[aid]
        print(f"   list entry: only-gc {sorted(set(a)-set(b))}; only-official {sorted(set(b)-set(a))}; value diffs {[k for k in set(a)&set(b) if a[k]!=b[k]]}")
    def fit(side, name):
        p = f"{B}/{side}/{name}"
        if not os.path.exists(p): return None
        z = zipfile.ZipFile(p); return hashlib.sha256(z.read(z.namelist()[0])).hexdigest()[:16]
    a, b = fit("gc", f"activity_fit_{aid}.zip"), fit("official", f"activity_fit_{aid}.zip")
    print(f"   FIT sha gc={a} official={b} identical={a==b}")
