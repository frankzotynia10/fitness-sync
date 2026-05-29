import psycopg2
import psycopg2.extras
from psycopg2 import sql
from config import (
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD,
    DB_WRITE_HOST, DB_WRITE_PORT, DB_WRITE_NAME,
    DB_WRITE_USER, DB_WRITE_PASSWORD,
)


def get_conn():
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD,
    )
    conn.set_session(readonly=True, autocommit=True)
    return conn


def get_write_conn():
    if not DB_WRITE_USER or not DB_WRITE_PASSWORD:
        raise RuntimeError("DB_WRITE_USER / DB_WRITE_PASSWORD are not configured.")
    conn = psycopg2.connect(
        host=DB_WRITE_HOST, port=DB_WRITE_PORT, dbname=DB_WRITE_NAME,
        user=DB_WRITE_USER, password=DB_WRITE_PASSWORD,
    )
    conn.autocommit = False
    return conn


def run_query(sql_text: str, params: tuple = ()) -> list:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql_text, params)
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def run_query_one(sql_text: str, params: tuple = ()) -> dict:
    rows = run_query(sql_text, params)
    return rows[0] if rows else {}


def run_query_composed(query, params: tuple = ()) -> list:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def run_scalar(sql_text: str, params: tuple = ()):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql_text, params)
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def run_write(sql_text: str, params: tuple = ()):
    conn = get_write_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql_text, params)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def run_write_returning_one(sql_text: str, params: tuple = ()) -> dict:
    conn = get_write_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql_text, params)
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else {}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# -------------------------------------------------------------------
# Schema introspection helpers
# -------------------------------------------------------------------

def relation_exists(relation_name: str, schema_name: str = "public") -> bool:
    from utils import validate_identifier
    validate_identifier(relation_name)
    validate_identifier(schema_name)
    reg = run_scalar("select to_regclass(%s)", (f"{schema_name}.{relation_name}",))
    return reg is not None


def dataset_exists(dataset_name: str, schema_name: str = "public") -> bool:
    from utils import validate_identifier
    validate_identifier(dataset_name)
    validate_identifier(schema_name)
    return bool(run_scalar("""
        select exists (
            select 1 from information_schema.tables
            where table_schema = %s and table_name = %s
        )
    """, (schema_name, dataset_name)))


def view_exists(view_name: str, schema_name: str = "public") -> bool:
    from utils import validate_identifier
    validate_identifier(view_name)
    validate_identifier(schema_name)
    return bool(run_scalar("""
        select exists (
            select 1 from information_schema.views
            where table_schema = %s and table_name = %s
        )
    """, (schema_name, view_name)))


def get_dataset_columns(dataset_name: str, schema_name: str = "public") -> set:
    from utils import validate_identifier
    validate_identifier(dataset_name)
    validate_identifier(schema_name)
    rows = run_query("""
        select column_name from information_schema.columns
        where table_schema = %s and table_name = %s
    """, (schema_name, dataset_name))
    return {row["column_name"] for row in rows}
