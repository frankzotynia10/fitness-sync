# Garmin Personal API spike: findings (2026-11-14)

**Verdict: worth adopting for the reads it covers, but it cannot replace `garminconnect`.**
The official API covers roughly two-thirds of the `garmin_daily` columns (daily summary, sleep summary, HRV, body battery, stress, weight, VO2 max) and everything garmin-sync stores for activities, including the original FIT. Training readiness, training status and load, endurance score, recovery time, race predictions, FTP and the intraday sleep epochs are not available. So any move would be a hybrid: official API first, `garminconnect` for the rest. The unofficial login has to stay either way, because hevy2garmin writes to Garmin.

## How it works (verified)
- `POST https://personal.connectapi.garmin.com/personalapitoken-service/personal-api/personal-api-key/token` with `Authorization: ApiKey <key>` and **`Accept: */*`**. `Accept: application/json` returns **406**.
- The response is **`text/plain`: the bare JWT**, not JSON. It lasts about 24h and there is no refresh, so get a new token each run.
- Data calls: `Authorization: Bearer <jwt>`, same host, same paths as Connect's internal API.
- The gateway is an allowlist: **every unlisted path returns 404**, even ones `garminconnect` uses on the same service.
- About 35 calls this session; no throttling seen. Key lives in `/mnt/user/appdata/fitness-sync/garmin/.env` on Andromeda. The running `garmin-sync` container doesn't have it in its env.

## Activity FIT: not stripped
For a recent walk activity, the `.fit` inside the API zip is **byte-identical** to the `garminconnect` ORIGINAL download (same SHA-256, 46,610 bytes). It includes the undocumented messages, such as 140 (physiological metrics) and the session `unknown_*` fields. Only the zip wrapper differs (timestamp). One activity tested; no cycling or power file yet.

## Field coverage vs garmin-sync

| garmin-sync data | Official API | Source |
|---|---|---|
| Steps, distance, floors, calories (total/active/BMR), RHR, min/max HR | ✅ | `dailysummaries` v2 (nested: `movement.*`, `calories.*`, `heartRate.*`) |
| Stress avg/max + durations (incl. uncategorized) | ✅ | `dailysummaries` v2 `stress.*` |
| Body battery charged/drained/high/low/latest | ✅ | `dailysummaries` v2 `bodyBattery.*` |
| Body battery at wake / during sleep | ❌ | not seen |
| Respiration avg, SpO2 avg/min | ✅ | `dailysummaries` v2 `respiration.*`, `pulseOx.*` |
| Intensity minutes | ✅ | `dailysummaries` v2 |
| Active/sedentary/sleeping seconds, skin temp deviation | ❌ | not in v2 summary |
| Sleep duration, stages, score, sleep stress | ✅ | `dailySleep`, plus sleep need/alignment that we don't store yet |
| Sleep epochs (levels, movement, HR, SpO2, resp, stress, BB, HRV) → `sleep intraday` | ❌ | `dailySleep` has no epoch arrays; `dailySleepData` 404 |
| HRV last-night avg, weekly, baseline, status | ✅ | `hrv/daily/{s}/{e}` |
| HRV intraday readings | ✅ | `hrv/{date}` → `hrvReadings` |
| HR intraday | ✅ | `dailyHeartRate` → `heartRateValues` |
| Body battery intraday | ✅ | `bodyBattery/reports/daily` → `bodyBatteryValuesArray` |
| Stress / respiration / steps intraday | ❌ | `dailyStress`, `daily/respiration`, `dailySummaryChart` 404 |
| Weight + body composition | ✅ | `weight/range` (`allWeightMetrics`) |
| VO2 max | ✅ | `maxmet/daily` (sparse: only days it changed; use a long window) |
| Heat acclimation | ❌ | not in `maxmet` response |
| Training readiness, training status, acute/chronic load, ACWR, load balance | ❌ | 404 |
| Endurance score, recovery time, race predictions | ❌ | 404 |
| FTP | ❌ | 404. `hr_zones` gives LTHR and zone floors only |
| Activity list fields (name, type, distance, durations, elevation, speed, HR, calories, TE aerobic/anaerobic, training load, intensity mins, HR time-in-zone, BB delta) | ✅ | `activities/search/activities` |
| Per-activity HR zone boundaries | ⚠️ | `hrTimeInZones` 404; derive from `biometric-service/heartRateZones/v2` |
| Activity FIT (records, power, etc.) | ✅ | `download-service/files/activity/{id}` (zip) |

## Recommendation
1. **Don't change the pipeline yet.** The token-based `garminconnect` path still covers everything, and the API can't remove the dependency.
2. If `garminconnect` auth breaks again, the official API is a solid fallback for about two-thirds of `garmin_daily`, all of `garmin_activities` (including FIT/GPS/HR records), and HR/BB/HRV intraday. A small `services/official.py` behind a feature flag would cover that.
3. Low-effort win if wanted: pull `sleepNeed`/`sleepAlignment` and the HRV baseline/status into `garmin_daily`. We don't store these today, and they're cheap with this API.
4. Key expiry: keys last 30 to 365 days. Add an expiry date to the staleness watchdog if this ever goes into production.

## Reproduce
```
# on Andromeda, key stays on the server
docker run --rm -v $PWD:/probe -v /mnt/user/appdata/fitness-sync/garmin/.env:/probe/.env:ro \
  -w /probe --entrypoint python ghcr.io/frankzotynia10/garmin-sync:latest probe.py --days 7 --fit
```
Raw responses go to `out/` (gitignored: personal health data).
