from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from mcp_data_analyst.catalog import to_identifier
from mcp_data_analyst.cli import parse_settings
from mcp_data_analyst.engine import to_jsonable
from mcp_data_analyst.profiling import profile_dataframe


def test_cli_flags_override_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MCP_DA_MAX_ROWS", "42")
    monkeypatch.setenv("MCP_DA_PORT", "9999")
    settings = parse_settings(["--data-dir", str(tmp_path), "--port", "8106", "--transport", "streamable-http"])
    assert settings.max_rows == 42
    assert settings.port == 8106
    assert settings.transport == "streamable-http"
    assert settings.data_dir == tmp_path.resolve()


def test_invalid_settings_exit_with_usage_error() -> None:
    with pytest.raises(SystemExit):
        parse_settings(["--memory-limit", "1GB; DROP TABLE x"])


@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        (("Sales 2024",), "sales_2024"),
        (("2024-report",), "t_2024_report"),
        (("shop", "Order Items"), "shop_order_items"),
    ],
)
def test_identifiers(parts: tuple[str, ...], expected: str) -> None:
    assert to_identifier(*parts) == expected


def test_to_jsonable_handles_special_values() -> None:
    assert to_jsonable(float("nan")) is None
    assert to_jsonable(pd.Timestamp("2025-01-02").to_pydatetime()) == "2025-01-02T00:00:00"
    assert to_jsonable(b"\x01\x02") == "0102"


def test_profile_dataframe_detects_issues() -> None:
    rng = np.random.default_rng(0)
    values = np.concatenate([rng.normal(10, 1, 200), [500.0]])
    df = pd.DataFrame(
        {
            "value": values,
            "twice": values * 2,
            "constant": ["x"] * 201,
            "maybe": [None] * 30 + ["a"] * 171,
        }
    )
    df = pd.concat([df, df.iloc[[0]]], ignore_index=True)
    profile = profile_dataframe("t", len(df), df, {"value": "DOUBLE"})
    kinds = {(i.kind, i.column) for i in profile.issues}
    assert profile.duplicate_rows == 1
    assert ("outliers", "value") in kinds
    assert ("constant", "constant") in kinds
    assert ("missing", "maybe") in kinds
    assert profile.top_correlations[0].pearson == pytest.approx(1.0)
    by_name = {c.name: c for c in profile.columns}
    assert by_name["value"].numeric is not None and by_name["value"].numeric.max == 500.0
    assert by_name["maybe"].top_values is not None and by_name["maybe"].top_values[0].value == "a"
