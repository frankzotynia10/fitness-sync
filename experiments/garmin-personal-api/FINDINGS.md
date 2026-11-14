# Garmin Personal API spike: findings (2026-11-14)

**Verdict: for everything both sources expose, the official API returns the same data as `garminconnect`. It cannot replace `garminconnect` because the daily training metrics are missing, but per-activity recovery time, VO2 max and training load can be recovered from the FIT file.**

Method: pulled the same 7 days (Nov 8–14) and 5 activities (2 walks, 2 strength sessions, 1 Zwift ride) from both sources on Andromeda in one run. Then compared values day by day, searched every official payload for every garminconnect training value (any key name, unit conversions included), and decoded the undocumented FIT messages. Scripts: `compare_pull.py` and `analysis/`.

## 1. Overlap: same data
| Check | Result |
|---|---|
| Daily values (steps, distance, floors, kcal total/active/BMR, RHR, stress avg/max/durations, body battery, intensity mins, respiration, SpO2, sleep duration/stages/score/stress, HRV, weight) | **202/227 identical**. Every miss is min/max HR, plus one floors value (3.74 vs 3). |
| Min/max HR | garmin-sync reads them from `get_stats`, which differs by 1–7 bpm. The official value **matches garminconnect's own `get_heart_rates`** exactly, so this is a definition difference, not lost data. |
| Intraday HR / HRV readings / Body Battery | **Identical, point for point**, all 7 days (≈720 HR points/day) |
| Activity detail `summaryDTO` | **Identical keys and values** for all 5 activities, including the ride |
| Activity list entries | Same values. The official version lacks `hasPolyline`, `deviceId`, `timeZoneId`, `hasSplits`, `hasHeatMap`, `purposeful`, `userRoles`. **garmin-sync uses `hasPolyline`** to decide whether to parse the FIT, so a port would need another signal. |
| Original FIT files | **Byte-identical** (SHA-256) for all 5 activities. Nothing is stripped. |

## 2. Training metrics: missing from the endpoints
Every garminconnect value from training readiness, training status, load balance, race predictions, FTP and lactate threshold was searched across all 72 official payloads.
- **Present:** VO2 max (`maxmet/daily`, sparse: days it changed) and lactate-threshold HR (`heartRateZones/v2` → `lactateThresholdHeartRateUsed`).
- **Absent under any name:** readiness score, factors and feedback; recovery time; acute/chronic load, ACWR, training status phrase; monthly load balance; race predictions; FTP (only power-based thresholds in zones); endurance and hill score. Apparent hits were coincidences (e.g. recovery time 461 = one activity's calories, FTP 180 = one night's awake seconds).
- Unlisted paths (`trainingreadiness`, `trainingstatus`, `racepredictions`, FTP, `dailySleepData`, `dailyStress`, respiration, steps chart) return **404**: the gateway serves only its 27 documented endpoints.

## 3. Training data inside the FIT (watch-recorded activities only)
Undocumented message 140 ("physiological metrics") in the walk's FIT, decoded against garminconnect:

| FIT field | Decoded | garminconnect value | Match |
|---|---|---|---|
| 140.9 | 5351 min | readiness entry at activity end (`AFTER_POST_EXERCISE_RESET`, 16:19): `recoveryTime` 5351 | ✅ exact |
| 140.7 | 861684 / 65536 × 3.5 = **46.0** | VO2 max 46.0 | ✅ |
| 140.3 / .26 / .37 / .39, session.168 | 177593 / 65536 = **2.71** | `activityTrainingLoad` 2.71 | ✅ |
| 140.31 | 64 | morning readiness score 64 | ⚠️ plausible, unconfirmed |

So after each watch activity you get post-activity recovery time, VO2 max and that activity's load. Acute/chronic load, training status and readiness factors are not in the file. The Zwift ride's FIT carries no msg 140 (Zwift wrote the file, not the watch).

## 4. Undocumented API behavior
- Token: `POST .../personal-api-key/token`, `Authorization: ApiKey <key>`, **`Accept: */*`** (`application/json` → 406). The response is **text/plain: a bare JWT**, valid about 24h, no refresh.
- Data calls: `Authorization: Bearer <jwt>`, same host and paths as Connect's internal API. About 100 calls today, no throttling.
- Key is `GARMIN_PERSONAL_KEY` in `/mnt/user/appdata/fitness-sync/garmin/.env`. It isn't in the running container's env.
- Listed but empty for this account: wellness activity summaries. Food logs, hydration, fitness age and weight goals respond (not compared).

## Recommendation
1. **No pipeline change now.** `garminconnect` still covers everything, and hevy2garmin needs it to write anyway.
2. **Fallback plan if `garminconnect` auth breaks:** the official API covers all activity data, the intraday HR/HRV/BB series, and about two-thirds of `garmin_daily` with identical values. Add recovery time and VO2 max from FIT msg 140. What would be lost: readiness, training status/load/ACWR, load balance, race predictions, FTP, skin temp, the intraday sleep/stress/respiration/steps series, and body battery at wake.
3. Cheap extra regardless of what we decide: `sleepNeed`/`sleepAlignment` and the HRV baseline/status aren't stored today.

## Reproduce (on Andromeda; key and tokens stay on the server)
```
docker run --rm -v $PWD:/probe -w /probe --entrypoint python \
  -v /mnt/user/appdata/fitness-sync/garmin/.env:/probe/.env:ro \
  -v /mnt/user/appdata/fitness-sync/garmin/tokens:/root/.garminconnect:ro \
  ghcr.io/frankzotynia10/garmin-sync:latest compare_pull.py --days 7
```
Then run `analysis/side_by_side.py cmp`, `analysis/value_match.py cmp` and `analysis/fit_training_fields.py <activity ids>` (the last runs in the same image; it needs `fitparse`). Raw payloads go to `cmp/` and `out/`, both gitignored because they hold personal health data.
