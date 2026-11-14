"""Garmin Connect Personal API client (official, read-only).

Key: GARMIN_PERSONAL_KEY (created at connect.garmin.com > Settings > Personal API Key).
Exchanged for a ~24h bearer token on construction; there is no refresh token.

Method names mirror the garminconnect calls whose payloads are identical
(heart rate, HRV, activities, FIT download), so the existing sync code can take
either client. See experiments/garmin-personal-api/FINDINGS.md for the comparison.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

HOST = os.environ.get("GARMIN_PERSONAL_HOST", "https://personal.connectapi.garmin.com")
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"


class OfficialApiError(RuntimeError):
    pass


class _DownloadFormat:
    ORIGINAL = "ORIGINAL"


class OfficialClient:
    is_official = True
    ActivityDownloadFormat = _DownloadFormat

    def __init__(self, api_key: str | None = None) -> None:
        key = (api_key or os.environ.get("GARMIN_PERSONAL_KEY") or "").strip()
        if not key:
            raise OfficialApiError("GARMIN_PERSONAL_KEY is not set")
        # The token endpoint 406s on "Accept: application/json" and answers text/plain (bare JWT).
        body = self._request("POST", TOKEN_PATH, {"Authorization": f"ApiKey {key}", "Accept": "*/*"})
        text = body.decode("utf-8").strip()
        try:
            parsed = json.loads(text)
            token = parsed.get("access_token") if isinstance(parsed, dict) else parsed
        except ValueError:
            token = text.strip('"')
        if not token or str(token).count(".") != 2:
            raise OfficialApiError("token endpoint did not return a JWT")
        self._auth = {"Authorization": f"Bearer {token}"}
        self._cache: dict = {}

    # ── transport ────────────────────────────────────────────
    # Rate limit observed 2026-11-14: 100 requests per rolling window, 429 with
    # {"retryInSeconds": N}. A normal 3-day run makes ~30 calls; retry covers bursts.
    MAX_RETRIES = 3

    @classmethod
    def _request(cls, method: str, path: str, headers: dict, params: dict | None = None) -> bytes:
        url = HOST + path + ("?" + urllib.parse.urlencode(params) if params else "")
        req = urllib.request.Request(url, method=method, headers={"Accept": "application/json", **headers})
        for attempt in range(cls.MAX_RETRIES + 1):
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return resp.read()
            except urllib.error.HTTPError as e:
                body = e.read()
                if e.code == 429 and attempt < cls.MAX_RETRIES:
                    try:
                        wait = int(json.loads(body).get("retryInSeconds", 30))
                    except Exception:
                        wait = 30
                    print(f"  [official] rate limited on {path}; retrying in {wait + 1}s")
                    time.sleep(min(wait, 120) + 1)
                    continue
                raise OfficialApiError(f"{method} {path}: HTTP {e.code} {body[:200]!r}") from None
        raise OfficialApiError(f"{method} {path}: retries exhausted")

    def get(self, path: str, **params):
        # Per-run cache: daily and intraday sync ask for some payloads (e.g. HRV) twice.
        key = (path, tuple(sorted(params.items())))
        if key not in self._cache:
            body = self._request("GET", path, self._auth, params or None)
            self._cache[key] = json.loads(body) if body else None
        return self._cache[key]

    # ── garminconnect-compatible (identical payloads) ────────
    def get_heart_rates(self, day: str):
        return self.get("/wellness-service/wellness/dailyHeartRate", date=day)

    def get_hrv_data(self, day: str):
        return self.get(f"/hrv-service/hrv/{day}")

    def get_activities_by_date(self, start: str, end: str):
        return self.get("/activitylist-service/activities/search/activities",
                        start=0, limit=100, startDate=start, endDate=end)

    def download_activity(self, activity_id, dl_fmt=None) -> bytes:
        return self._request("GET", f"/download-service/files/activity/{activity_id}",
                             {**self._auth, "Accept": "*/*"})

    # ── official-only shapes ─────────────────────────────────
    def daily_summary(self, day: str) -> dict | None:
        data = (self.get("/usersummary-service/v2/dailysummaries", startDate=day, endDate=day) or {}).get("data") or []
        return next((d for d in data if d.get("calendarDate") == day), None)

    def daily_sleep(self, day: str):
        return self.get("/sleep-service/sleep/dailySleep", date=day)

    def weight_range(self, start: str, end: str):
        return self.get(f"/weight-service/weight/range/{start}/{end}", includeAll="true")

    def vo2_max(self, start: str, end: str):
        return self.get(f"/metrics-service/metrics/maxmet/daily/{start}/{end}")

    def heart_rate_zones(self):
        return self.get("/biometric-service/heartRateZones/v2")
