"""Transport-agnostic implementation of every tool. The MCP layer in ``server.py`` is a thin wrapper."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime
from pathlib import Path
from typing import Literal

import pandas as pd

from .catalog import quote_ident
from .charts import ChartType, render_chart
from .config import Settings
from .engine import QueryEngine, to_jsonable
from .errors import InvalidArgumentError
from .models import (
    ColumnStats,
    DatasetDescription,
    DatasetInfo,
    DatasetList,
    DatasetProfile,
    ExportResult,
    QueryResult,
)
from .profiling import profile_dataframe
from .sandbox import safe_output_path

log = logging.getLogger(__name__)

MAX_CHART_ROWS = 50_000
MAX_SAMPLE_ROWS = 50


def _default_name(prefix: str, suffix: str) -> str:
    return f"{prefix}-{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}{suffix}"


class Analyst:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.engine = QueryEngine(settings)

    def close(self) -> None:
        self.engine.close()

    # -- discovery -----------------------------------------------------------------------

    def list_datasets(self) -> DatasetList:
        return DatasetList(datasets=list(self.engine.datasets.values()))

    def schema(self, name: str) -> DatasetInfo:
        return self.engine.dataset(name)

    def describe(self, name: str, sample_rows: int = 5) -> DatasetDescription:
        info = self.engine.dataset(name)
        if not 0 <= sample_rows <= MAX_SAMPLE_ROWS:
            raise InvalidArgumentError(f"sample_rows must be between 0 and {MAX_SAMPLE_ROWS}.")
        table = quote_ident(info.name)
        stats: list[ColumnStats] = []
        if info.columns:
            parts = ", ".join(
                f"count(*) - count({quote_ident(c.name)}), count(DISTINCT {quote_ident(c.name)})" for c in info.columns
            )
            (row,) = self.engine.scalar_rows(f"SELECT {parts} FROM {table}")
            for i, col in enumerate(info.columns):
                nulls, distinct = int(row[2 * i]), int(row[2 * i + 1])
                stats.append(
                    ColumnStats(
                        name=col.name,
                        type=col.type,
                        null_count=nulls,
                        null_pct=round(100 * nulls / info.row_count, 2) if info.row_count else 0.0,
                        distinct_count=distinct,
                    )
                )
        sample = self.engine.scalar_rows(f"SELECT * FROM {table} LIMIT {int(sample_rows)}") if sample_rows else []
        names = [c.name for c in info.columns]
        return DatasetDescription(
            name=info.name,
            source=info.source,
            format=info.format,
            row_count=info.row_count,
            columns=stats,
            sample_rows=[{k: to_jsonable(v) for k, v in zip(names, r, strict=True)} for r in sample],
        )

    # -- analysis ------------------------------------------------------------------------

    def query(self, sql: str, max_rows: int | None = None) -> QueryResult:
        return self.engine.query(sql, max_rows)

    def profile(self, name: str, columns: list[str] | None = None) -> DatasetProfile:
        info = self.engine.dataset(name)
        df, _ = self.engine.table_df(name, self.settings.max_profile_rows)
        if columns:
            unknown = sorted(set(columns) - set(df.columns))
            if unknown:
                raise InvalidArgumentError(f"Unknown column(s) {unknown} in {name!r}. Available: {list(df.columns)}.")
            df = df[columns]
        types = {c.name: c.type for c in info.columns}
        return profile_dataframe(info.name, info.row_count, df, types)

    def create_chart(
        self,
        sql: str,
        chart_type: ChartType,
        x: str,
        y: str | None = None,
        title: str | None = None,
        bins: int = 20,
        filename: str | None = None,
    ) -> tuple[bytes, Path, int, bool]:
        """Render a chart of the query result. Returns (png, saved path, rows plotted, truncated)."""
        target = safe_output_path(self.settings.output_dir, filename or _default_name("chart", ".png"), {".png"})
        df, truncated = self.engine.query_df(sql, MAX_CHART_ROWS)
        png = render_chart(df, chart_type, x, y, title, bins)
        target.write_bytes(png)
        log.info("chart written", extra={"path": str(target), "rows": len(df), "chart_type": chart_type})
        return png, target, len(df), truncated

    def export(self, sql: str, fmt: Literal["csv", "xlsx"] = "csv", filename: str | None = None) -> ExportResult:
        suffix = f".{fmt}"
        target = safe_output_path(self.settings.output_dir, filename or _default_name("export", suffix), {suffix})
        df, truncated = self.engine.query_df(sql, self.settings.max_export_rows)
        if fmt == "csv":
            df.to_csv(target, index=False)
        else:
            _strip_timezones(df).to_excel(target, index=False, engine="openpyxl")
        log.info("export written", extra={"path": str(target), "rows": len(df)})
        return ExportResult(path=str(target), format=fmt, rows_written=len(df), truncated=truncated)


def _strip_timezones(df: pd.DataFrame) -> pd.DataFrame:
    """Excel cannot store timezone-aware datetimes; convert them to naive UTC."""
    out = df.copy()
    for col in out.columns:
        if isinstance(out[col].dtype, pd.DatetimeTZDtype):
            out[col] = out[col].dt.tz_convert("UTC").dt.tz_localize(None)
    return out
