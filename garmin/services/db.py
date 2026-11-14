from __future__ import annotations

import os
import re

import psycopg2


def get_connection():
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ.get("DB_NAME", "postgres"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ["DB_PASSWORD"],
    )


def get_existing_columns(cur, table_name: str = "garmin_daily", schema_name: str = "public") -> set:
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = %s
        ORDER BY ordinal_position
        """,
        (schema_name, table_name),
    )
    return {row[0] for row in cur.fetchall()}


class RecordingConnection:
    """Dry-run wrapper: SELECTs hit the real DB, writes are recorded instead of executed.

    Each recorded write is {"table": ..., "row": {column: value}} parsed from the
    INSERT statement, so two runs can be diffed row by row.
    """

    def __init__(self, real):
        self._real = real
        self.writes: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return _RecordingCursor(self)

    def close(self):
        self._real.close()


class _RecordingCursor:
    _INSERT = re.compile(r"INSERT\s+INTO\s+([\w.\"]+)\s*\((.*?)\)\s*VALUES", re.I | re.S)

    def __init__(self, rec: RecordingConnection):
        self._rec = rec
        self._cur = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self._cur is not None:
            self._cur.close()
        return False

    def execute(self, query, params=None):
        text = query.as_string(self._rec._real) if hasattr(query, "as_string") else query
        if text.lstrip().upper().startswith("SELECT"):
            self._cur = self._rec._real.cursor()
            self._cur.execute(text, params)
            return
        m = self._INSERT.search(text)
        if not m:
            self._rec.writes.append({"table": None, "sql": text[:200], "params": list(params or [])})
            return
        table = m.group(1).replace('"', "").split(".")[-1]
        cols = [c.strip().strip('"') for c in m.group(2).split(",")]
        self._rec.writes.append({"table": table, "row": dict(zip(cols, params or []))})

    def fetchone(self):
        return self._cur.fetchone() if self._cur else None

    def fetchall(self):
        return self._cur.fetchall() if self._cur else []
