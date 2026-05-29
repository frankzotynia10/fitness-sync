import re

IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

DISALLOWED_SQL_PATTERNS = [
    r"\bINSERT\b", r"\bUPDATE\b", r"\bDELETE\b", r"\bDROP\b",
    r"\bALTER\b", r"\bCREATE\b", r"\bTRUNCATE\b", r"\bGRANT\b",
    r"\bREVOKE\b", r"\bCOPY\b", r"\bCALL\b", r"\bVACUUM\b",
    r"\bANALYZE\b", r"\bCOMMENT\b", r"\bREFRESH\b", r"\bMERGE\b",
    r"\bDO\b", r"\bSET\b", r"\bRESET\b",
]


def validate_identifier(name: str) -> str:
    if not IDENTIFIER_RE.match(name):
        raise ValueError(f"Invalid identifier: {name}")
    return name


def clamp_limit(value: int, min_value: int, max_value: int) -> int:
    return max(min_value, min(max_value, value))


def ensure_readonly_sql(sql_text: str) -> str:
    text = sql_text.strip()
    if not text:
        raise ValueError("SQL cannot be empty.")
    if ";" in text:
        raise ValueError("Only single-statement read-only SQL is allowed.")
    upper = text.upper().lstrip()
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        raise ValueError("Only SELECT or WITH queries are allowed.")
    for pattern in DISALLOWED_SQL_PATTERNS:
        if re.search(pattern, upper, flags=re.IGNORECASE):
            raise ValueError(f"Disallowed SQL detected: {pattern}")
    return text
