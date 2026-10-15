from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from mcp import Client

from mcp_data_analyst.analyst import Analyst
from mcp_data_analyst.config import Settings
from mcp_data_analyst.server import create_server

DEMO_DIR = Path(__file__).resolve().parents[1] / "data" / "demo"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def output_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("output")


@pytest.fixture(scope="session")
def analyst(output_dir: Path) -> Iterator[Analyst]:
    instance = Analyst(Settings(data_dir=DEMO_DIR, output_dir=output_dir, max_rows=100, query_timeout_s=5))
    yield instance
    instance.close()


@pytest.fixture
async def client(analyst: Analyst) -> AsyncIterator[Client]:
    """An MCP client talking to the server through the SDK's in-memory transport."""
    async with Client(create_server(analyst)) as c:
        yield c
