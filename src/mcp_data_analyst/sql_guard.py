"""Static validation of user SQL with sqlglot, before it ever reaches DuckDB.

This is the first of two layers: the DuckDB connection itself is also read-only with
file-system access disabled (see ``engine.py``). The guard exists so that clients get
a precise, early error message and so that a single engine misconfiguration is not
enough to turn the server into a read/write primitive.
"""

from __future__ import annotations

from collections.abc import Collection

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from .errors import QueryError, SecurityError

# Any of these nodes anywhere in the tree means the statement is not a pure read.
_FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Copy,
    exp.Attach,
    exp.Detach,
    exp.Install,
    exp.Pragma,
    exp.Set,
    exp.Use,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Command,
    exp.LoadData,
    exp.TruncateTable,
)

# Table functions that are harmless (they generate data, they do not read files).
_ALLOWED_TABLE_FUNCTIONS = {"range", "generate_series", "unnest"}

# Scalar/table functions that touch the file system, environment or engine internals.
_FORBIDDEN_FUNCTIONS = {"glob", "getenv", "query", "query_table", "load_extension"}
_FORBIDDEN_FUNCTION_PREFIXES = ("read_", "parquet_", "sqlite_", "duckdb_", "pragma_", "iceberg_", "delta_")


def _function_name(node: exp.Expression) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    if isinstance(node, exp.Func):
        return node.sql_name().lower()
    return ""


def validate_select(sql: str, allowed_tables: Collection[str]) -> exp.Expression:
    """Return the parsed statement if ``sql`` is exactly one read-only query, else raise.

    ``allowed_tables`` are the registered dataset names; any other table reference
    (including quoted file paths such as ``FROM 'secret.csv'``) is rejected.
    """
    if not sql or not sql.strip():
        raise QueryError("The SQL query is empty.")
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except ParseError as exc:
        raise QueryError(f"Could not parse SQL: {exc.errors[0]['description'] if exc.errors else exc}") from exc

    if len(statements) != 1:
        raise SecurityError(f"Exactly one SQL statement is allowed, got {len(statements)}.")
    statement = statements[0]

    if not isinstance(statement, exp.Select | exp.SetOperation):
        raise SecurityError(f"Only read-only SELECT queries are allowed (got {type(statement).__name__.upper()}).")

    for node in statement.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise SecurityError(f"Forbidden SQL construct: {type(node).__name__.upper()}.")
        name = _function_name(node)
        if name and (name in _FORBIDDEN_FUNCTIONS or name.startswith(_FORBIDDEN_FUNCTION_PREFIXES)):
            raise SecurityError(f"Function {name!r} is not allowed.")

    _check_tables(statement, {t.lower() for t in allowed_tables})
    return statement


def _check_tables(statement: exp.Expression, allowed: set[str]) -> None:
    cte_names = {cte.alias_or_name.lower() for cte in statement.find_all(exp.CTE)}
    for table in statement.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            func = _function_name(table.this)
            if func in _ALLOWED_TABLE_FUNCTIONS:
                continue
            raise SecurityError(f"Table function {func or table.this.sql()!r} is not allowed in FROM.")
        if table.catalog or (table.db and table.db.lower() != "main"):
            raise SecurityError(f"Qualified table names are not allowed: {table.sql()!r}.")
        name = table.name.lower()
        if name not in allowed and name not in cte_names:
            known = ", ".join(sorted(allowed)) or "(none)"
            raise SecurityError(f"Unknown table {table.name!r}. Query only registered datasets: {known}.")
