from psycopg2 import sql
from db import run_query, run_query_composed
from utils import validate_identifier, clamp_limit, ensure_readonly_sql


def register(mcp):

    @mcp.tool()
    def list_available_datasets() -> list:
        """List all readable tables and views in non-system schemas."""
        return run_query("""
            select table_schema, table_name, table_type
            from information_schema.tables
            where table_schema not in ('pg_catalog', 'information_schema')
            order by table_schema, table_name
        """)

    @mcp.tool()
    def describe_dataset(dataset_name: str, schema_name: str = "public") -> list:
        """Describe columns, types, nullability, and defaults for a table/view."""
        validate_identifier(dataset_name)
        validate_identifier(schema_name)
        return run_query("""
            select column_name, data_type, is_nullable, column_default, ordinal_position
            from information_schema.columns
            where table_schema = %s and table_name = %s
            order by ordinal_position
        """, (schema_name, dataset_name))

    @mcp.tool()
    def preview_dataset(dataset_name: str, schema_name: str = "public", limit: int = 25) -> list:
        """Return sample rows from any readable table/view."""
        validate_identifier(dataset_name)
        validate_identifier(schema_name)
        limit = clamp_limit(limit, 1, 200)
        query = sql.SQL("select * from {}.{} limit {}").format(
            sql.Identifier(schema_name),
            sql.Identifier(dataset_name),
            sql.Literal(limit),
        )
        return run_query_composed(query)

    @mcp.tool()
    def query_readonly(sql_text: str, limit: int = 200) -> list:
        """Run a read-only SELECT/WITH query against the database."""
        safe_sql = ensure_readonly_sql(sql_text)
        limit = clamp_limit(limit, 1, 500)
        return run_query(f"select * from ({safe_sql}) as q limit %s", (limit,))

    @mcp.tool()
    def search_columns(column_search: str) -> list:
        """Search for tables/views containing column names matching a pattern."""
        return run_query("""
            select table_schema, table_name, column_name, data_type
            from information_schema.columns
            where table_schema not in ('pg_catalog', 'information_schema')
              and column_name ilike %s
            order by table_schema, table_name, ordinal_position
        """, (f"%{column_search}%",))
