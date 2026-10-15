"""Read-only DuckDB query engine with row limits and timeouts."""

from __future__ import annotations

import datetime as dt
import decimal
import logging
import math
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import duckdb
import pandas as pd

from .catalog import build_catalog, quote_ident
from .config import Settings
from .errors import DatasetNotFoundError, QueryError, QueryTimeoutError
from .models import DatasetInfo, QueryResult
from .sql_guard import validate_select

log = logging.getLogger(__name__)

T = TypeVar("T")


def to_jsonable(value: Any) -> Any:
    """Convert DuckDB/pandas scalars into JSON-serialisable Python values."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return value.total_seconds()
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, list | tuple):
        return [to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if value is pd.NaT:
        return None
    return str(value)


class QueryEngine:
    """Owns the catalog file and a read-only DuckDB connection over it."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._tmpdir = Path(tempfile.mkdtemp(prefix="mcp-da-"))
        catalog_path = self._tmpdir / "catalog.duckdb"
        try:
            datasets = build_catalog(settings.data_dir, catalog_path, settings.max_file_size_bytes)
        except Exception:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
            raise
        self.datasets: dict[str, DatasetInfo] = {d.name: d for d in datasets}
        self._con = duckdb.connect(
            str(catalog_path),
            read_only=True,
            config={
                "enable_external_access": False,  # no file reads/writes, no ATTACH, no extension installs
                "autoload_known_extensions": False,
                "memory_limit": settings.memory_limit,
                "lock_configuration": True,  # SET cannot undo any of the above
            },
        )

    def close(self) -> None:
        self._con.close()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def dataset(self, name: str) -> DatasetInfo:
        try:
            return self.datasets[name]
        except KeyError:
            known = ", ".join(sorted(self.datasets)) or "(none)"
            raise DatasetNotFoundError(f"Unknown dataset {name!r}. Available datasets: {known}.") from None

    # -- execution ---------------------------------------------------------------------

    def query(self, sql: str, max_rows: int | None = None) -> QueryResult:
        """Validate and run user SQL, returning at most ``max_rows`` rows."""
        validate_select(sql, self.datasets.keys())
        limit = min(max_rows or self.settings.max_rows, self.settings.max_rows)
        return self._run(sql, limit)

    def query_df(self, sql: str, max_rows: int) -> tuple[pd.DataFrame, bool]:
        """Validate and run user SQL into a DataFrame (used by charts and exports)."""
        validate_select(sql, self.datasets.keys())
        return self._run_df(sql, max_rows)

    def table_df(self, name: str, max_rows: int) -> tuple[pd.DataFrame, bool]:
        """Load a registered dataset (reservoir-sampled if larger than ``max_rows``)."""
        info = self.dataset(name)
        sql = f"SELECT * FROM {quote_ident(info.name)}"
        if info.row_count > max_rows:
            sql += f" USING SAMPLE reservoir({int(max_rows)} ROWS) REPEATABLE (42)"
        df, _ = self._run_df(sql, max_rows)
        return df, info.row_count > max_rows

    def scalar_rows(self, sql: str, params: list[Any] | None = None) -> list[tuple[Any, ...]]:
        """Run trusted, internally generated SQL (never user input)."""
        return self._with_timeout(lambda cur: cur.execute(sql, params or []).fetchall())

    def _run(self, sql: str, limit: int) -> QueryResult:
        start = time.perf_counter()

        def work(cur: duckdb.DuckDBPyConnection) -> tuple[list[str], list[str], list[tuple[Any, ...]]]:
            cur.execute(sql)
            desc = cur.description or []
            return [d[0] for d in desc], [str(d[1]) for d in desc], cur.fetchmany(limit + 1)

        columns, types, rows = self._with_timeout(work)
        truncated = len(rows) > limit
        rows = rows[:limit]
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        log.info("query executed", extra={"rows": len(rows), "truncated": truncated, "elapsed_ms": elapsed})
        return QueryResult(
            columns=columns,
            column_types=types,
            rows=[[to_jsonable(v) for v in row] for row in rows],
            row_count=len(rows),
            truncated=truncated,
            elapsed_ms=elapsed,
        )

    def _run_df(self, sql: str, limit: int) -> tuple[pd.DataFrame, bool]:
        # LIMIT on an outer query keeps memory bounded; the inner ORDER BY is preserved by DuckDB.
        wrapped = f"SELECT * FROM ({sql}) AS _q LIMIT {int(limit) + 1}"
        df = self._with_timeout(lambda cur: cur.execute(wrapped).df())
        truncated = len(df) > limit
        return df.head(limit), truncated

    def _with_timeout(self, work: Callable[[duckdb.DuckDBPyConnection], T]) -> T:
        cur = self._con.cursor()
        timer = threading.Timer(self.settings.query_timeout_s, cur.interrupt)
        timer.start()
        try:
            return work(cur)
        except duckdb.InterruptException as exc:
            raise QueryTimeoutError(
                f"Query exceeded the {self.settings.query_timeout_s:g}s timeout and was cancelled."
            ) from exc
        except duckdb.Error as exc:
            raise QueryError(_clean_duckdb_error(exc)) from exc
        finally:
            timer.cancel()
            cur.close()


def _clean_duckdb_error(exc: duckdb.Error) -> str:
    """DuckDB's message (which usually includes helpful hints), bounded in size."""
    message = " ".join(str(exc).split()) or type(exc).__name__
    return message[:600]
