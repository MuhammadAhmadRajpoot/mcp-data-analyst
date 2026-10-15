"""Discover files in the data dir and materialise them into a private DuckDB catalog file.

Every supported file becomes one table (SQLite files and Excel workbooks contribute
one table per table/sheet). The catalog is written once with a read-write connection
that is then closed; the query engine re-opens it strictly read-only.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

import duckdb
import pandas as pd

from .models import ColumnInfo, DatasetInfo, FileFormat
from .sandbox import iter_data_files

log = logging.getLogger(__name__)

_FORMATS: dict[str, FileFormat] = {
    ".csv": "csv",
    ".tsv": "csv",
    ".parquet": "parquet",
    ".xlsx": "excel",
    ".sqlite": "sqlite",
    ".sqlite3": "sqlite",
    ".db": "sqlite",
}


def to_identifier(*parts: str) -> str:
    """Turn file/sheet names into a lowercase SQL-friendly identifier, e.g. ``Sales 2024`` -> ``sales_2024``."""
    raw = "_".join(p for p in parts if p)
    ident = re.sub(r"[^0-9a-zA-Z]+", "_", raw).strip("_").lower() or "dataset"
    return f"t_{ident}" if ident[0].isdigit() else ident


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _load_frames(path: Path, fmt: FileFormat) -> list[tuple[str | None, pd.DataFrame]]:
    """Read formats DuckDB cannot read natively (offline) into DataFrames."""
    if fmt == "excel":
        sheets = pd.read_excel(path, sheet_name=None, engine="openpyxl")
        return list(sheets.items())
    # mode=ro: the source database is never opened for writing.
    uri = f"{path.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        tables = [
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        ]
        frames = [(t, pd.read_sql_query(f"SELECT * FROM {quote_ident(t)}", conn)) for t in tables]
    conn.close()
    return frames


def build_catalog(data_dir: Path, catalog_path: Path, max_file_size_bytes: int) -> list[DatasetInfo]:
    """Materialise every safe data file into ``catalog_path`` and describe the result."""
    root = data_dir.resolve()
    datasets: list[DatasetInfo] = []
    con = duckdb.connect(str(catalog_path))
    try:
        for path in iter_data_files(root, max_file_size_bytes):
            fmt = _FORMATS[path.suffix.lower()]
            rel = path.relative_to(root).as_posix()
            try:
                datasets.extend(_register_file(con, path, rel, fmt, {d.name for d in datasets}))
            except Exception as exc:  # one unreadable file must not take the server down
                log.warning("could not load file", extra={"path": rel, "error": str(exc)})
        con.execute("CHECKPOINT")
    finally:
        con.close()
    log.info("catalog built", extra={"datasets": len(datasets), "data_dir": str(root)})
    return datasets


def _register_file(
    con: duckdb.DuckDBPyConnection, path: Path, rel: str, fmt: FileFormat, taken: set[str]
) -> list[DatasetInfo]:
    stem = to_identifier(Path(rel).with_suffix("").as_posix())
    created: list[DatasetInfo] = []

    def unique(name: str) -> str | None:
        if name in taken or any(d.name == name for d in created):
            log.warning("duplicate dataset name, skipping", extra={"name": name, "path": rel})
            return None
        return name

    if fmt in ("csv", "parquet"):
        reader = "read_parquet" if fmt == "parquet" else "read_csv_auto"
        if name := unique(stem):
            con.execute(f"CREATE TABLE {quote_ident(name)} AS SELECT * FROM {reader}({_sql_literal(str(path))})")
            created.append(_describe(con, name, rel, fmt, None))
        return created

    frames = _load_frames(path, fmt)
    for sub, frame in frames:
        # A single-sheet workbook is simply named after the file.
        base = stem if fmt == "excel" and len(frames) == 1 else to_identifier(stem, str(sub))
        if name := unique(base):
            con.register("_incoming", frame)
            con.execute(f"CREATE TABLE {quote_ident(name)} AS SELECT * FROM _incoming")
            con.unregister("_incoming")
            created.append(_describe(con, name, rel, fmt, str(sub)))
    return created


def _describe(con: duckdb.DuckDBPyConnection, name: str, rel: str, fmt: FileFormat, sub: str | None) -> DatasetInfo:
    cols = con.execute(
        "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = ? ORDER BY ordinal_position",
        [name],
    ).fetchall()
    (rows,) = con.execute(f"SELECT count(*) FROM {quote_ident(name)}").fetchone() or (0,)
    return DatasetInfo(
        name=name,
        source=rel,
        format=fmt,
        sub_table=sub,
        row_count=int(rows),
        columns=[ColumnInfo(name=c, type=t) for c, t in cols],
    )
