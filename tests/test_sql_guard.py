from __future__ import annotations

import pytest

from mcp_data_analyst.errors import QueryError, SecurityError
from mcp_data_analyst.sql_guard import validate_select

TABLES = {"orders", "customers"}


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM orders",
        "select o.* from orders o join customers c using (customer_id) where c.country = 'PK'",
        "WITH t AS (SELECT customer_id FROM orders) SELECT count(*) FROM t",
        "SELECT 1 UNION ALL SELECT 2",
        "FROM orders SELECT status",
        "SELECT * FROM main.orders",
        "SELECT * FROM range(10)",
        "SELECT status, row_number() OVER (PARTITION BY status ORDER BY order_id) FROM orders",
        "SELECT 'drop table orders; --' AS harmless_string",
        "SELECT * FROM orders -- ; DROP TABLE orders",
        "SELECT * FROM ORDERS",
    ],
)
def test_allows_read_only_queries(sql: str) -> None:
    validate_select(sql, TABLES)


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("DROP TABLE orders", "Only read-only SELECT"),
        ("DELETE FROM orders", "Only read-only SELECT"),
        ("UPDATE orders SET status = 'x'", "Only read-only SELECT"),
        ("INSERT INTO orders SELECT * FROM orders", "Only read-only SELECT"),
        ("CREATE TABLE x AS SELECT 1", "Only read-only SELECT"),
        ("CREATE TEMP TABLE x AS SELECT 1", "Only read-only SELECT"),
        ("ALTER TABLE orders ADD COLUMN x INT", "Only read-only SELECT"),
        ("COPY orders TO 'stolen.csv'", "Only read-only SELECT"),
        ("ATTACH 'other.db'", "Only read-only SELECT"),
        ("INSTALL httpfs", "Only read-only SELECT"),
        ("PRAGMA database_list", "Only read-only SELECT"),
        ("SET enable_external_access = true", "Only read-only SELECT"),
        ("SELECT 1; DROP TABLE orders", "Exactly one SQL statement"),
        ("SELECT 1; SELECT 2", "Exactly one SQL statement"),
        ("SELECT * FROM 'C:/secrets/passwords.csv'", "Unknown table"),
        ("SELECT * FROM '../../etc/passwd'", "Unknown table"),
        ("SELECT * FROM read_csv_auto('/etc/passwd')", "not allowed"),
        ("SELECT * FROM read_parquet('x.parquet')", "not allowed"),
        ("SELECT read_text('/etc/passwd')", "not allowed"),
        ("SELECT * FROM glob('*')", "not allowed"),
        ("SELECT getenv('GROQ_API_KEY')", "not allowed"),
        ("SELECT * FROM duckdb_settings()", "not allowed"),
        ("SELECT * FROM sqlite_scan('x.db', 't')", "not allowed"),
        ("SELECT * FROM other_db.main.orders", "Qualified table names"),
        ("SELECT * FROM secret_schema.orders", "Qualified table names"),
        ("SELECT * FROM products", "Unknown table 'products'"),
        ("SELECT * FROM (SELECT * FROM read_json('x'))", "not allowed"),
    ],
)
def test_blocks_unsafe_queries(sql: str, message: str) -> None:
    with pytest.raises(SecurityError, match=message):
        validate_select(sql, TABLES)


@pytest.mark.parametrize("sql", ["", "   ", "SELEC * FROM orders WHERE (", "SELECT * FROM orders WHERE"])
def test_rejects_empty_or_unparseable(sql: str) -> None:
    with pytest.raises(QueryError):
        validate_select(sql, TABLES)
