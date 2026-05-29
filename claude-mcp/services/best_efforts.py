from __future__ import annotations

from collections import deque
from typing import Iterable


DEFAULT_WINDOWS = {
    "5s": 5,
    "1m": 60,
    "5m": 300,
    "20m": 1200,
}


def _normalize_power_series(values: Iterable[int | float | None]) -> list[int]:
    """
    Normalize a power series to integers.

    Missing values are converted to 0.
    This is acceptable for a first pass and keeps the rolling window logic simple.
    If you later want stricter handling for gaps/dropouts, this is the place to refine it.
    """
    normalized: list[int] = []

    for value in values:
        if value is None:
            normalized.append(0)
        else:
            normalized.append(int(round(value)))

    return normalized


def rolling_best_average(
    power_values: Iterable[int | float | None],
    window_sec: int,
) -> float | None:
    """
    Compute the best rolling average power over a fixed-size window.

    Assumes the incoming series is approximately 1 Hz data.
    """
    series = _normalize_power_series(power_values)

    if len(series) < window_sec or window_sec <= 0:
        return None

    q: deque[int] = deque()
    running_sum = 0
    best_avg: float | None = None

    for value in series:
        q.append(value)
        running_sum += value

        if len(q) > window_sec:
            running_sum -= q.popleft()

        if len(q) == window_sec:
            avg = running_sum / window_sec
            if best_avg is None or avg > best_avg:
                best_avg = avg

    return best_avg


def compute_best_efforts(
    power_values: Iterable[int | float | None],
    windows: dict[str, int] | None = None,
) -> dict[str, float | None]:
    """
    Compute named best-effort rolling averages.

    Example output:
        {
            "5s": 812.4,
            "1m": 466.2,
            "5m": 322.7,
            "20m": 278.6
        }
    """
    if windows is None:
        windows = DEFAULT_WINDOWS

    return {
        label: rolling_best_average(power_values, seconds)
        for label, seconds in windows.items()
    }


def compute_best_efforts_by_seconds(
    power_values: Iterable[int | float | None],
    window_seconds: list[int],
) -> dict[int, float | None]:
    """
    Same as compute_best_efforts, but keyed by numeric seconds.

    Example:
        {
            5: 812.4,
            60: 466.2,
            300: 322.7,
            1200: 278.6
        }
    """
    unique_windows = sorted(set(window_seconds))
    return {
        seconds: rolling_best_average(power_values, seconds)
        for seconds in unique_windows
    }