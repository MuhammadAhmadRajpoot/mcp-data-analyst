"""Launch the real console entry point as a subprocess and talk to it over stdio."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from .conftest import DEMO_DIR

pytestmark = pytest.mark.anyio


async def test_stdio_server_end_to_end(tmp_path: Path) -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_data_analyst", "--data-dir", str(DEMO_DIR), "--output-dir", str(tmp_path)],
        env={**os.environ, "MCP_DA_LOG_LEVEL": "WARNING"},
    )
    async with Client(params) as client:
        assert client.server_info is not None and client.server_info.name == "mcp-data-analyst"
        result = await client.call_tool("query", {"sql": "SELECT count(*) AS n FROM support_tickets"})
        assert result.structured_content["rows"] == [[600]]
        blocked = await client.call_tool("query", {"sql": "COPY support_tickets TO 'leak.csv'"})
        assert blocked.is_error
