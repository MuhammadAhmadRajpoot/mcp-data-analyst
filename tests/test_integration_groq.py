"""Optional end-to-end test: real Groq model + real stdio server subprocess.

Run with:  pytest -m integration   (needs GROQ_API_KEY in the environment or .env)
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_agent():
    spec = importlib.util.spec_from_file_location("groq_agent", ROOT / "examples" / "groq_agent.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses need the module registered
    spec.loader.exec_module(module)
    return module


@pytest.mark.integration
@pytest.mark.anyio
async def test_groq_agent_answers_with_tool_calls(tmp_path: Path) -> None:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    if not os.getenv("GROQ_API_KEY"):
        pytest.skip("GROQ_API_KEY not set")
    agent = _load_agent()
    run = await agent.run_agent(
        "How many customers are in the customers dataset? Use the query tool.",
        model=os.getenv("GROQ_MODEL", agent.DEFAULT_MODEL),
        data_dir=ROOT / "data" / "demo",
        output_dir=tmp_path,
    )
    assert any(name == "query" for name, _ in run.tool_calls)
    assert "400" in run.answer
