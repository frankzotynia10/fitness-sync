"""Experimental probe for the Garmin Connect Personal API (self-serve API key, Oct 2026).

Trades GARMIN_PERSONAL_KEY for a 24h access token, then hits a set of read-only
endpoints and dumps each raw response to ./personal_api_out/ so we can compare
the payloads with what the garminconnect (unofficial) client gives us today.

Stdlib only — no extra installs needed.

    python personal_api_probe.py               # last 3 days
    python personal_api_probe.py --days 7
    python personal_api_probe.py --fit         # also download latest activity file

Key (GARMIN_PERSONAL_KEY or GARMIN_PERSONAL_API_KEY) is read from the environment,
falling back to ./.env, garmin/.env, or the repo-root .env.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

HOST = os.environ.get("GARMIN_PERSONAL_HOST", "https://personal.connectapi.garmin.com")
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "out"  # gitignored; raw personal health data


def load_key() -> str:
    names = ("GARMIN_PERSONAL_KEY", "GARMIN_PERSONAL_API_KEY")
    for name in names:
        if os.environ.get(name):
            return os.environ[name].strip()
    repo = HERE.parent.parent
    for env_file in (HERE / ".env", repo / "garmin" / ".env", repo / ".env"):
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.split("=", 1)[0] in names:
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("GARMIN_PERSONAL_KEY not set (env, ./.env, garmin/.env, or repo-root .env).")


def http(method: str, url: str, headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, method=method, headers={"Accept": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()


def get_token(key: str) -> str:
    # Token endpoint 406s on "Accept: application/json"; Garmin's sample curl sends */*.
    status, headers, body = http("POST", HOST + TOKEN_PATH,
                                 {"Authorization": f"ApiKey {key}", "Accept": "*/*"})
    print(f"Token endpoint content-type: {headers.get('Content-Type')}")
    if status != 200:
        sys.exit(f"Token exchange failed: HTTP {status}: {body[:500]!r}")
    text = body.decode("utf-8").strip()
    try:
        data = json.loads(text)
    except ValueError:
        data = {"access_token": text.strip('"')}  # text/plain: body is the raw JWT
    token = data.get("access_token") or data.get("accessToken") if isinstance(data, dict) else data
    if not token or token.count(".") != 2:
        sys.exit(f"Unexpected token response ({len(text)} chars, not a JWT)")
    try:  # JWT exp, for info only
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        exp = dt.datetime.fromtimestamp(claims["exp"])
        print(f"Got access token (expires {exp:%Y-%m-%d %H:%M}). Token response keys: {list(data)}")
    except Exception:
        print(f"Got access token. Token response keys: {list(data)}")
    return token


def endpoints(start: dt.date, end: dt.date) -> dict[str, str]:
    s, e, today = start.isoformat(), end.isoformat(), end.isoformat()
    return {
        "profile_settings": "/userprofile-service/userprofile/settings",
        "daily_summaries": f"/usersummary-service/v2/dailysummaries?startDate={s}&endDate={e}",
        "sleep_today": f"/sleep-service/sleep/dailySleep?date={today}",
        "sleep_range": f"/sleep-service/sleep/dailySleepSummariesByDate?startDate={s}&endDate={e}",
        "hrv_range": f"/hrv-service/hrv/daily/{s}/{e}",
        "stress_range": f"/usersummary-service/stats/stress/daily/{s}/{e}",
        "steps_range": f"/usersummary-service/stats/steps/daily/{s}/{e}",
        "body_battery": f"/wellness-service/wellness/bodyBattery/reports/daily?startDate={s}&endDate={e}",
        "heart_rate_today": f"/wellness-service/wellness/dailyHeartRate?date={today}",
        "vo2max_range": f"/metrics-service/metrics/maxmet/daily/{s}/{e}",
        "weight_range": f"/weight-service/weight/range/{s}/{e}?includeAll=true",
        "hr_zones": "/biometric-service/heartRateZones/v2",
        "activities": f"/activitylist-service/activities/search/activities?start=0&limit=10&startDate={s}&endDate={e}",
    }


def summarize(body: bytes) -> str:
    try:
        data = json.loads(body)
    except ValueError:
        return f"{len(body)} bytes (non-JSON)"
    if isinstance(data, list):
        first = data[0] if data else None
        keys = list(first)[:12] if isinstance(first, dict) else []
        return f"list[{len(data)}] keys={keys}"
    if isinstance(data, dict):
        return f"dict keys={list(data)[:12]}"
    return repr(data)[:80]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--fit", action="store_true", help="download the most recent activity file")
    args = ap.parse_args()

    token = get_token(load_key())
    auth = {"Authorization": f"Bearer {token}"}
    end = dt.date.today()
    start = end - dt.timedelta(days=args.days - 1)
    OUT_DIR.mkdir(exist_ok=True)

    results: dict[str, int] = {}
    activities = None
    for name, path in endpoints(start, end).items():
        status, _, body = http("GET", HOST + path, auth)
        results[name] = status
        (OUT_DIR / f"{name}.json").write_bytes(body)
        info = summarize(body) if status == 200 else body[:200].decode("utf-8", "replace")
        print(f"[{status}] {name:18} {info}")
        if name == "activities" and status == 200:
            activities = json.loads(body)

    if args.fit and activities:
        act_id = activities[0].get("activityId")
        status, headers, body = http("GET", f"{HOST}/download-service/files/activity/{act_id}",
                                     {**auth, "Accept": "*/*"})
        ext = ".zip" if body[:2] == b"PK" else ".fit"
        out = OUT_DIR / f"activity_{act_id}{ext}"
        out.write_bytes(body)
        print(f"[{status}] activity file      {len(body)} bytes -> {out.name} "
              f"(content-type {headers.get('Content-Type')})")

    ok = sum(1 for s in results.values() if s == 200)
    print(f"\n{ok}/{len(results)} endpoints returned 200. Raw responses in {OUT_DIR}")


if __name__ == "__main__":
    main()
