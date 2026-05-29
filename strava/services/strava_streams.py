from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    if value.endswith("Z"):
        value = value.replace("Z", "+00:00")

    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def normalize_strava_streams(
    payload: dict[str, Any],
    activity_start_time: datetime | None = None,
) -> list[dict[str, Any]]:
    """
    Normalize Strava stream payload into a common activity stream row format.
    """
    time_stream = payload.get("time", {}).get("data", []) or []
    watts_stream = payload.get("watts", {}).get("data", []) or []
    hr_stream = payload.get("heartrate", {}).get("data", []) or []
    cadence_stream = payload.get("cadence", {}).get("data", []) or []
    distance_stream = payload.get("distance", {}).get("data", []) or []
    speed_stream = payload.get("velocity_smooth", {}).get("data", []) or []

    rows: list[dict[str, Any]] = []

    for i, offset in enumerate(time_stream):
        ts = None

        if activity_start_time is not None:
            base_time = activity_start_time
            if base_time.tzinfo is None:
                base_time = base_time.replace(tzinfo=timezone.utc)
            ts = base_time + timedelta(seconds=int(offset))

        rows.append(
            {
                "timestamp": ts,
                "time_offset_sec": int(offset),
                "power_w": watts_stream[i] if i < len(watts_stream) else None,
                "heart_rate_bpm": hr_stream[i] if i < len(hr_stream) else None,
                "cadence_rpm": cadence_stream[i] if i < len(cadence_stream) else None,
                "distance_m": distance_stream[i] if i < len(distance_stream) else None,
                "speed_mps": speed_stream[i] if i < len(speed_stream) else None,
            }
        )

    return rows


def parse_activity_start_time(activity_start_time_iso: str | None) -> datetime | None:
    return _parse_iso_datetime(activity_start_time_iso)