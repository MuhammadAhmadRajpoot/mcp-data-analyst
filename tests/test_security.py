"""Sandbox, read-only engine and resource-limit guarantees."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pytest
from mcp import Client

from mcp_data_analyst.analyst import Analyst
from mcp_data_analyst.config import Settings
from mcp_data_analyst.engine import QueryEngine
from mcp_data_analyst.errors import QueryError, QueryTimeoutError, SecurityError
from mcp_data_analyst.sandbox import safe_output_path


def _symlink_or_skip(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("creating symlinks is not permitted on this machine")


@pytest.fixture
def tiny_data_dir(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    (data / "nested").mkdir(parents=True)
    pd.DataFrame({"a": [1, 2, 3]}).to_csv(data / "small.csv", index=False)
    pd.DataFrame({"b": range(20_000)}).to_csv(data / "nested" / "Big File.csv", index=False)
    (data / "notes.txt").write_text("ignored: unsupported extension")
    return data


def test_discovers_nested_files_and_sanitises_names(tiny_data_dir: Path, tmp_path: Path) -> None:
    engine = QueryEngine(Settings(data_dir=tiny_data_dir, output_dir=tmp_path / "out"))
    try:
        assert set(engine.datasets) == {"small", "nested_big_file"}
        assert engine.datasets["nested_big_file"].source == "nested/Big File.csv"
    finally:
        engine.close()


def test_skips_files_over_size_limit(tiny_data_dir: Path, tmp_path: Path) -> None:
    engine = QueryEngine(Settings(data_dir=tiny_data_dir, output_dir=tmp_path / "out", max_file_size_mb=0.01))
    try:
        assert set(engine.datasets) == {"small"}
    finally:
        engine.close()


def test_skips_symlink_pointing_outside_data_dir(tiny_data_dir: Path, tmp_path: Path) -> None:
    outside = tmp_path / "secret.csv"
    pd.DataFrame({"password": ["hunter2"]}).to_csv(outside, index=False)
    _symlink_or_skip(tiny_data_dir / "leak.csv", outside)
    engine = QueryEngine(Settings(data_dir=tiny_data_dir, output_dir=tmp_path / "out"))
    try:
        assert "leak" not in engine.datasets
    finally:
        engine.close()


def test_missing_data_dir_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SecurityError, match="does not exist"):
        QueryEngine(Settings(data_dir=tmp_path / "nope", output_dir=tmp_path / "out"))


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE pwned AS SELECT 1",
        "INSERT INTO products SELECT * FROM products",
        "COPY products TO 'stolen.csv'",
        "SELECT * FROM read_csv_auto('pyproject.toml')",
        "SET enable_external_access = true",
        "ATTACH 'other.duckdb'",
    ],
)
def test_engine_is_read_only_even_without_the_sql_guard(analyst: Analyst, sql: str) -> None:
    """Second defence layer: raw SQL that bypasses sqlglot is still refused by DuckDB itself."""
    with pytest.raises(QueryError):
        analyst.engine._run(sql, 10)


def test_query_timeout_interrupts_long_queries(tiny_data_dir: Path, tmp_path: Path) -> None:
    engine = QueryEngine(Settings(data_dir=tiny_data_dir, output_dir=tmp_path / "out", query_timeout_s=0.3))
    try:
        with pytest.raises(QueryTimeoutError, match="timeout"):
            engine.query("SELECT count(*) FROM range(100000000000) a, range(1000) b")
    finally:
        engine.close()


@pytest.mark.parametrize(
    "filename",
    [
        "../escape.csv",
        "..\\escape.csv",
        "sub/dir.csv",
        "/tmp/abs.csv",
        "C:\\Windows\\x.csv",
        ".hidden.csv",
        "a..csv",
        "",
    ],
)
def test_output_filenames_cannot_escape(tmp_path: Path, filename: str) -> None:
    with pytest.raises(SecurityError):
        safe_output_path(tmp_path, filename, {".csv"})


def test_output_extension_is_enforced(tmp_path: Path) -> None:
    with pytest.raises(SecurityError, match="extension"):
        safe_output_path(tmp_path, "payload.exe", {".csv"})


def test_output_refuses_to_follow_symlinks(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    _symlink_or_skip(out / "report.csv", tmp_path / "victim.csv")
    with pytest.raises(SecurityError):
        safe_output_path(out, "report.csv", {".csv"})


@pytest.mark.anyio
@pytest.mark.parametrize("filename", ["../../evil.csv", "nested/evil.csv"])
async def test_export_tool_rejects_traversal(client: Client, output_dir: Path, filename: str) -> None:
    result = await client.call_tool("export_result", {"sql": "SELECT 1 AS x", "filename": filename})
    assert result.is_error and "Invalid file name" in result.content[0].text
    assert not (output_dir.parent / "evil.csv").exists()


@pytest.mark.anyio
async def test_query_tool_returns_security_errors_as_tool_errors(client: Client) -> None:
    result = await client.call_tool("query", {"sql": "SELECT * FROM 'C:/Windows/win.ini'"})
    assert result.is_error
    assert "Unknown table" in result.content[0].text
