"""Every tool, called end-to-end through an in-memory MCP client session."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pandas as pd
import pytest
from mcp import Client
from mcp_types import ImageContent, TextContent

from mcp_data_analyst.analyst import Analyst
from mcp_data_analyst.server import create_server

pytestmark = pytest.mark.anyio

EXPECTED_DATASETS = {"customers", "orders", "order_items", "products", "support_tickets"}


async def test_lists_all_six_tools(client: Client) -> None:
    tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == {"list_datasets", "describe_dataset", "query", "profile", "create_chart", "export_result"}
    assert tools["query"].annotations is not None and tools["query"].annotations.read_only_hint is True
    assert "sql" in tools["query"].input_schema["required"]


async def test_list_datasets_covers_every_file_format(client: Client) -> None:
    result = await client.call_tool("list_datasets", {})
    assert not result.is_error
    datasets = {d["name"]: d for d in result.structured_content["datasets"]}
    assert set(datasets) == EXPECTED_DATASETS
    assert {d["format"] for d in datasets.values()} == {"csv", "parquet", "excel", "sqlite"}
    assert datasets["support_tickets"]["sub_table"] == "tickets"
    assert datasets["customers"]["row_count"] == 400


async def test_describe_dataset(client: Client) -> None:
    result = await client.call_tool("describe_dataset", {"name": "customers", "sample_rows": 3})
    assert not result.is_error
    body = result.structured_content
    columns = {c["name"]: c for c in body["columns"]}
    assert columns["email"]["null_count"] == 14  # injected by the demo generator
    assert columns["customer_id"]["distinct_count"] == 400
    assert len(body["sample_rows"]) == 3
    assert set(body["sample_rows"][0]) == set(columns)


async def test_describe_unknown_dataset_is_a_tool_error(client: Client) -> None:
    result = await client.call_tool("describe_dataset", {"name": "nope"})
    assert result.is_error
    assert "Unknown dataset 'nope'" in result.content[0].text
    assert "customers" in result.content[0].text  # lists what is available


async def test_query_join_and_aggregate(client: Client) -> None:
    sql = """
        WITH delivered AS (SELECT order_id FROM orders WHERE status = 'delivered')
        SELECT p.category, round(sum(i.quantity * i.unit_price * (1 - i.discount)), 2) AS revenue
        FROM order_items i JOIN delivered d USING (order_id) JOIN products p USING (product_id)
        GROUP BY 1 ORDER BY 2 DESC
    """
    result = await client.call_tool("query", {"sql": sql})
    assert not result.is_error
    body = result.structured_content
    assert body["columns"] == ["category", "revenue"]
    assert body["row_count"] == 6
    revenues = [r[1] for r in body["rows"]]
    assert revenues == sorted(revenues, reverse=True)
    assert body["truncated"] is False


async def test_query_row_limit_sets_truncated(client: Client) -> None:
    result = await client.call_tool("query", {"sql": "SELECT * FROM order_items", "max_rows": 7})
    assert result.structured_content["row_count"] == 7
    assert result.structured_content["truncated"] is True


async def test_query_cannot_exceed_server_row_limit(client: Client) -> None:
    result = await client.call_tool("query", {"sql": "SELECT * FROM order_items", "max_rows": 10_000})
    assert result.structured_content["row_count"] == 100  # conftest sets max_rows=100


async def test_query_serialises_dates(client: Client) -> None:
    result = await client.call_tool("query", {"sql": "SELECT min(order_date) AS first FROM orders"})
    assert result.structured_content["rows"][0][0].startswith("2024-03-01")


async def test_query_reports_sql_errors(client: Client) -> None:
    result = await client.call_tool("query", {"sql": "SELECT no_such_column FROM orders"})
    assert result.is_error
    assert "no_such_column" in result.content[0].text


async def test_profile_finds_injected_quality_issues(client: Client) -> None:
    result = await client.call_tool("profile", {"name": "order_items"})
    assert not result.is_error
    body = result.structured_content
    assert body["duplicate_rows"] == 9
    kinds = {(i["kind"], i["column"]) for i in body["issues"]}
    assert ("duplicates", None) in kinds
    assert ("outliers", "quantity") in kinds
    quantity = next(c for c in body["columns"] if c["name"] == "quantity")
    assert quantity["numeric"]["max"] >= 60
    assert sum(b["count"] for b in quantity["numeric"]["histogram"]) == body["profiled_rows"]


async def test_profile_correlations_and_column_subset(client: Client) -> None:
    result = await client.call_tool("profile", {"name": "products", "columns": ["unit_price", "unit_cost"]})
    body = result.structured_content
    assert [c["name"] for c in body["columns"]] == ["unit_price", "unit_cost"]
    assert body["top_correlations"][0]["pearson"] > 0.9  # cost is derived from price


async def test_profile_unknown_column(client: Client) -> None:
    result = await client.call_tool("profile", {"name": "products", "columns": ["ghost"]})
    assert result.is_error and "ghost" in result.content[0].text


@pytest.mark.parametrize(
    ("chart_type", "args"),
    [
        (
            "bar",
            {
                "sql": "SELECT category, avg(unit_price) AS avg_price FROM products GROUP BY 1",
                "x": "category",
                "y": "avg_price",
            },
        ),
        ("bar", {"sql": "SELECT channel FROM orders", "x": "channel"}),
        (
            "line",
            {
                "sql": "SELECT date_trunc('month', order_date) AS m, count(*) AS n FROM orders GROUP BY 1",
                "x": "m",
                "y": "n",
            },
        ),
        ("scatter", {"sql": "SELECT unit_price, unit_cost FROM products", "x": "unit_price", "y": "unit_cost"}),
        ("histogram", {"sql": "SELECT resolution_hours FROM support_tickets", "x": "resolution_hours"}),
    ],
)
async def test_create_chart_returns_image_and_saves_png(
    client: Client, output_dir: Path, chart_type: str, args: dict[str, str]
) -> None:
    filename = f"test-{chart_type}-{len(args)}.png"
    result = await client.call_tool("create_chart", {**args, "chart_type": chart_type, "filename": filename})
    assert not result.is_error, result.content
    text, image = result.content
    assert isinstance(text, TextContent) and isinstance(image, ImageContent)
    assert image.mime_type == "image/png"
    png = base64.b64decode(image.data)
    assert png.startswith(b"\x89PNG")
    saved = Path(json.loads(text.text)["saved_to"])
    assert saved == output_dir / filename and saved.read_bytes() == png


async def test_create_chart_validates_columns(client: Client) -> None:
    result = await client.call_tool(
        "create_chart", {"sql": "SELECT category FROM products", "chart_type": "scatter", "x": "category", "y": "nope"}
    )
    assert result.is_error and "nope" in result.content[0].text


async def test_create_chart_needs_numeric_histogram(client: Client) -> None:
    result = await client.call_tool(
        "create_chart", {"sql": "SELECT category FROM products", "chart_type": "histogram", "x": "category"}
    )
    assert result.is_error and "numeric" in result.content[0].text


@pytest.mark.parametrize("fmt", ["csv", "xlsx"])
async def test_export_result(client: Client, output_dir: Path, fmt: str) -> None:
    result = await client.call_tool(
        "export_result",
        {"sql": "SELECT * FROM customers ORDER BY customer_id", "format": fmt, "filename": f"customers.{fmt}"},
    )
    assert not result.is_error
    body = result.structured_content
    assert body["rows_written"] == 400 and body["truncated"] is False
    path = Path(body["path"])
    assert path.parent == output_dir
    frame = pd.read_csv(path) if fmt == "csv" else pd.read_excel(path)
    assert len(frame) == 400 and frame["customer_id"].iloc[0] == 1


async def test_export_generates_a_name_when_omitted(client: Client, output_dir: Path) -> None:
    result = await client.call_tool("export_result", {"sql": "SELECT 1 AS one"})
    path = Path(result.structured_content["path"])
    assert path.parent == output_dir and path.name.startswith("export-") and path.suffix == ".csv"


async def test_legacy_handshake_transport(analyst: Analyst) -> None:
    """The same server also works over the classic initialize-handshake JSON-RPC streams."""
    async with Client(create_server(analyst), mode="legacy") as legacy:
        result = await legacy.call_tool("query", {"sql": "SELECT count(*) AS n FROM products"})
        assert result.structured_content["rows"] == [[48]]
