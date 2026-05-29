from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from garmin_fit_sdk import Decoder, Stream


def _safe_get(record: dict[str, Any], key: str, default: Any = None) -> Any:
    """Safely get a field from a decoded FIT message dict."""
    return record.get(key, default)


def _to_datetime(value: Any) -> datetime | None:
    """Return value if it is already a datetime, else None."""
    return value if isinstance(value, datetime) else None


def decode_fit_messages(fit_path: str | Path) -> tuple[dict[str, Any], list[Any]]:
    """
    Decode a FIT file into Garmin FIT SDK message groups.

    Returns:
        (messages, errors)
    """
    stream = Stream.from_file(str(fit_path))
    decoder = Decoder(stream)
    messages, errors = decoder.read()
    return messages, errors


def parse_fit_records(fit_path: str | Path) -> list[dict[str, Any]]:
    """
    Parse FIT 'record' messages into normalized stream rows.

    Expected output row shape:
        {
            "timestamp": datetime | None,
            "time_offset_sec": int | None,
            "power_w": int | None,
            "heart_rate_bpm": int | None,
            "cadence_rpm": int | None,
            "distance_m": float | None,
            "speed_mps": float | None,
        }
    """
    messages, errors = decode_fit_messages(fit_path)

    # Don't hard fail on decoder warnings; some FIT files decode fine with minor issues.
    if errors:
        # You can replace this print with your logger if preferred.
        print(f"[fit_ingest] decode warnings/errors for {fit_path}: {errors}")

    # Garmin FIT SDK generally returns record messages under "record_mesgs"
    record_messages = messages.get("record_mesgs", [])
    if not record_messages:
        return []

    rows: list[dict[str, Any]] = []

    first_ts: datetime | None = None

    for rec in record_messages:
        ts = _to_datetime(_safe_get(rec, "timestamp"))
        if first_ts is None and ts is not None:
            first_ts = ts

        time_offset_sec: int | None = None
        if first_ts is not None and ts is not None:
            time_offset_sec = int((ts - first_ts).total_seconds())

        row = {
            "timestamp": ts,
            "time_offset_sec": time_offset_sec,
            "power_w": _safe_get(rec, "power"),
            "heart_rate_bpm": _safe_get(rec, "heart_rate"),
            "cadence_rpm": _safe_get(rec, "cadence"),
            "distance_m": _safe_get(rec, "distance"),
            "speed_mps": _safe_get(rec, "speed"),
        }
        rows.append(row)

    # Backfill offsets if timestamps were missing or irregular
    for idx, row in enumerate(rows):
        if row["time_offset_sec"] is None:
            row["time_offset_sec"] = idx

    return rows


def parse_fit_session_summary(fit_path: str | Path) -> dict[str, Any]:
    """
    Parse FIT session summary data if present.

    Returns:
        {
            "start_time": datetime | None,
            "total_timer_time_s": float | None,
            "total_elapsed_time_s": float | None,
            "avg_power_w": int | None,
            "max_power_w": int | None,
            "normalized_power_w": int | None,
            "training_stress_score": float | None,
        }
    """
    messages, errors = decode_fit_messages(fit_path)

    if errors:
        print(f"[fit_ingest] decode warnings/errors for {fit_path}: {errors}")

    session_messages = messages.get("session_mesgs", [])
    if not session_messages:
        return {}

    # Usually one primary session per activity
    session = session_messages[0]

    return {
        "start_time": _to_datetime(_safe_get(session, "start_time")),
        "total_timer_time_s": _safe_get(session, "total_timer_time"),
        "total_elapsed_time_s": _safe_get(session, "total_elapsed_time"),
        "avg_power_w": _safe_get(session, "avg_power"),
        "max_power_w": _safe_get(session, "max_power"),
        "normalized_power_w": _safe_get(session, "normalized_power"),
        "training_stress_score": _safe_get(session, "training_stress_score"),
    }


def extract_first_timestamp(fit_path: str | Path) -> datetime | None:
    """
    Convenience helper to find the first record timestamp in a FIT file.
    """
    rows = parse_fit_records(fit_path)
    if not rows:
        return None
    return rows[0].get("timestamp")