from __future__ import annotations

import datetime
import json
import sys


def dump_payload(label: str, payload, max_len: int = 30000) -> None:
    print(f"\n--- {label} ---")
    try:
        text = json.dumps(payload, indent=2, default=str)
        if len(text) > max_len:
            print(text[:max_len] + "\n... [truncated] ...")
        else:
            print(text)
    except Exception as e:
        print(f"Could not dump {label}: {e}")


def ts_from_ms(ms_value) -> datetime.datetime | None:
    """Convert millisecond epoch to UTC datetime."""
    if ms_value is None:
        return None
    try:
        return datetime.datetime.fromtimestamp(ms_value / 1000.0, tz=datetime.timezone.utc)
    except Exception:
        return None


def parse_gmt_str(s: str, fmt: str = "%Y-%m-%dT%H:%M:%S.%f") -> datetime.datetime | None:
    """Parse a Garmin GMT string to UTC-aware datetime."""
    if not s:
        return None
    try:
        dt = datetime.datetime.strptime(s, fmt)
        return dt.replace(tzinfo=datetime.timezone.utc)
    except Exception:
        try:
            dt = datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
            return dt.replace(tzinfo=datetime.timezone.utc)
        except Exception:
            return None


def deep_get(obj, *keys):
    cur = obj
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
        if cur is None:
            return None
    return cur


def first_non_null(*values):
    for v in values:
        if v is not None:
            return v
    return None


def recursive_find_first(obj, key_names):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in key_names and v is not None:
                return v
        for _, v in obj.items():
            found = recursive_find_first(v, key_names)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = recursive_find_first(item, key_names)
            if found is not None:
                return found
    return None


def normalize_weight_to_kg(raw_weight):
    if raw_weight is None:
        return None
    try:
        raw_weight = float(raw_weight)
    except Exception:
        return None
    if raw_weight > 500:
        return raw_weight / 1000.0
    return raw_weight


def normalize_mass_to_kg(raw_value):
    if raw_value is None:
        return None
    try:
        raw_value = float(raw_value)
    except Exception:
        return None
    if raw_value > 200:
        return raw_value / 1000.0
    return raw_value


def normalize_percentage(raw_value):
    if raw_value is None:
        return None
    try:
        return float(raw_value)
    except Exception:
        return None


def normalize_recovery_time_hours(raw_value):
    if raw_value is None:
        return None
    try:
        v = float(raw_value)
    except Exception:
        return None
    if v > 100000:
        return round(v / 3600000.0, 2)
    if v > 1000:
        return round(v / 3600.0, 2)
    if v > 72:
        return round(v / 60.0, 2)
    return round(v, 2)
