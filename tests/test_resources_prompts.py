from __future__ import annotations

import json

import pytest
from mcp import Client
from mcp.shared.exceptions import MCPError

pytestmark = pytest.mark.anyio


async def test_catalog_resource(client: Client) -> None:
    resources = (await client.list_resources()).resources
    assert [str(r.uri) for r in resources] == ["dataset://catalog"]
    result = await client.read_resource("dataset://catalog")
    names = {d["name"] for d in json.loads(result.contents[0].text)["datasets"]}
    assert "orders" in names


async def test_schema_resource_template(client: Client) -> None:
    templates = (await client.list_resource_templates()).resource_templates
    assert [t.uri_template for t in templates] == ["dataset://{name}/schema"]
    result = await client.read_resource("dataset://orders/schema")
    schema = json.loads(result.contents[0].text)
    assert schema["format"] == "parquet"
    assert [c["name"] for c in schema["columns"]] == ["order_id", "customer_id", "order_date", "status", "channel"]


async def test_schema_resource_unknown_dataset(client: Client) -> None:
    with pytest.raises(MCPError, match="Unknown dataset"):
        await client.read_resource("dataset://missing/schema")


async def test_prompts_are_listed(client: Client) -> None:
    prompts = {p.name: p for p in (await client.list_prompts()).prompts}
    assert set(prompts) == {"exploratory_analysis", "data_quality_report"}
    args = {a.name: a.required for a in prompts["exploratory_analysis"].arguments or []}
    assert args == {"dataset": True, "goal": False}


async def test_exploratory_analysis_prompt_embeds_schema(client: Client) -> None:
    result = await client.get_prompt("exploratory_analysis", {"dataset": "orders", "goal": "seasonality"})
    text = result.messages[0].content.text
    assert "Dataset `orders` (2500 rows" in text
    assert "- order_date: TIMESTAMP" in text
    assert "seasonality" in text


async def test_data_quality_prompt(client: Client) -> None:
    result = await client.get_prompt("data_quality_report", {"dataset": "customers"})
    text = result.messages[0].content.text
    assert "`profile` on `customers`" in text
    assert "- email: VARCHAR" in text
