"""Pydantic models shared by the engine and returned (as structured content) by the tools."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

FileFormat = Literal["csv", "parquet", "excel", "sqlite"]


class ColumnInfo(BaseModel):
    name: str
    type: str


class DatasetInfo(BaseModel):
    """A queryable table registered from a file in the data directory."""

    name: str = Field(description="SQL table name to use in queries.")
    source: str = Field(description="File path relative to the data directory.")
    format: FileFormat
    sub_table: str | None = Field(default=None, description="Excel sheet or SQLite table inside the file.")
    row_count: int
    columns: list[ColumnInfo]


class DatasetList(BaseModel):
    datasets: list[DatasetInfo]


class ColumnStats(BaseModel):
    name: str
    type: str
    null_count: int
    null_pct: float
    distinct_count: int


class DatasetDescription(BaseModel):
    name: str
    source: str
    format: FileFormat
    row_count: int
    columns: list[ColumnStats]
    sample_rows: list[dict[str, Any]]


class QueryResult(BaseModel):
    columns: list[str]
    column_types: list[str]
    rows: list[list[Any]]
    row_count: int = Field(description="Number of rows returned (after the row limit).")
    truncated: bool = Field(description="True if the result had more rows than the limit.")
    elapsed_ms: float


class HistogramBin(BaseModel):
    lower: float
    upper: float
    count: int


class NumericSummary(BaseModel):
    mean: float | None
    std: float | None
    min: float | None
    p25: float | None
    median: float | None
    p75: float | None
    max: float | None
    skew: float | None
    outlier_count: int = Field(description="Values outside 1.5 * IQR of the quartiles.")
    histogram: list[HistogramBin]


class ValueCount(BaseModel):
    value: str
    count: int


class ColumnProfile(BaseModel):
    name: str
    type: str
    null_count: int
    null_pct: float
    distinct_count: int
    numeric: NumericSummary | None = None
    top_values: list[ValueCount] | None = None
    min_value: str | None = Field(default=None, description="For date/time columns.")
    max_value: str | None = Field(default=None, description="For date/time columns.")


class Correlation(BaseModel):
    column_a: str
    column_b: str
    pearson: float


class QualityIssue(BaseModel):
    severity: Literal["info", "warning"]
    kind: Literal["duplicates", "missing", "constant", "outliers", "high_cardinality"]
    column: str | None
    detail: str


class DatasetProfile(BaseModel):
    name: str
    row_count: int
    profiled_rows: int = Field(description="Rows analysed; smaller than row_count if sampled.")
    duplicate_rows: int
    columns: list[ColumnProfile]
    top_correlations: list[Correlation]
    issues: list[QualityIssue]


class ExportResult(BaseModel):
    path: str
    format: Literal["csv", "xlsx"]
    rows_written: int
    truncated: bool
