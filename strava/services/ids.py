"""Strava ID helpers.

Strava is moving to IDs that may exceed 2^53. Its API sends every ID twice:
as a number (`id`) and as a string (`id_str`). Strava recommends reading the
string form. Python ints are exact either way, but reading `*_str` keeps us
safe if a JSON layer in between ever parses the number as a float.
"""


def strava_id(obj, key="id"):
    """Return obj[key] as an int, preferring the `<key>_str` field."""
    if not obj:
        return None
    s = obj.get(f"{key}_str")
    if s not in (None, ""):
        return int(s)
    v = obj.get(key)
    return int(v) if v is not None else None
