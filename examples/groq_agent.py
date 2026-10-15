"""Minimal MCP client agent: Groq tool calling over the mcp-data-analyst server (stdio).

It shows the *client* side of MCP: spawn the server as a subprocess, list its tools,
convert them to OpenAI-style function schemas, and let the model call them in a loop.

    pip install -e ".[examples]"
    cp .env.example .env            # then put your GROQ_API_KEY in .env
    python examples/groq_agent.py "Which product category earns the most revenue?"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp_types import CallToolResult, TextContent, Tool
from openai import BadRequestError, OpenAI
from openai.types.chat import ChatCompletionMessage

ROOT = Path(__file__).resolve().parents[1]
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "openai/gpt-oss-120b"
MAX_TOOL_RESULT_CHARS = 6_000
MAX_GENERATION_RETRIES = 3
SYSTEM_PROMPT = (
    "You are a data analyst with tools over local datasets. Always inspect the available datasets before "
    "writing SQL, use DuckDB SQL, and base every number in your answer on tool results. Answer concisely."
)


@dataclass
class AgentRun:
    answer: str
    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)


def to_openai_tool(tool: Tool) -> dict[str, Any]:
    """MCP tool definition -> OpenAI/Groq ``tools=[...]`` entry (the JSON Schema carries over unchanged)."""
    return {
        "type": "function",
        "function": {"name": tool.name, "description": tool.description or "", "parameters": tool.input_schema},
    }


def tool_result_to_text(result: CallToolResult) -> str:
    """Flatten an MCP tool result for the model: structured JSON if present, else text blocks."""
    if result.structured_content is not None and not result.is_error:
        text = json.dumps(result.structured_content, default=str)
    else:
        parts = [c.text if isinstance(c, TextContent) else f"[{c.type} content omitted]" for c in result.content]
        text = "\n".join(parts)
    return text[:MAX_TOOL_RESULT_CHARS]


def complete(
    llm: OpenAI, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
) -> ChatCompletionMessage:
    """One chat completion. Groq rejects generations that call a non-existent tool (``tool_use_failed``),
    which open-weight models occasionally produce; those are simply re-sampled."""
    for attempt in range(1, MAX_GENERATION_RETRIES + 1):
        try:
            response = llm.chat.completions.create(model=model, messages=messages, tools=tools, tool_choice="auto")
            return response.choices[0].message
        except BadRequestError as exc:
            if "tool_use_failed" not in str(exc) or attempt == MAX_GENERATION_RETRIES:
                raise
            print(
                f"   (model produced an invalid tool call, retrying {attempt}/{MAX_GENERATION_RETRIES - 1})", flush=True
            )
    raise AssertionError("unreachable")


def server_params(data_dir: Path, output_dir: Path) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_data_analyst", "--data-dir", str(data_dir), "--output-dir", str(output_dir)],
        env={**os.environ, "MCP_DA_LOG_LEVEL": "WARNING"},
    )


async def run_agent(question: str, model: str, data_dir: Path, output_dir: Path, max_steps: int = 8) -> AgentRun:
    llm = OpenAI(api_key=os.environ["GROQ_API_KEY"], base_url=GROQ_BASE_URL)
    async with Client(server_params(data_dir, output_dir)) as mcp:
        tools = [to_openai_tool(t) for t in (await mcp.list_tools()).tools]
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ]
        run = AgentRun(answer="")
        for _ in range(max_steps):
            message = complete(llm, model, messages, tools)
            if not message.tool_calls:
                run.answer = message.content or ""
                return run
            messages.append(message.model_dump(exclude_none=True, include={"role", "content", "tool_calls"}))
            for call in message.tool_calls:
                args = json.loads(call.function.arguments or "{}")
                run.tool_calls.append((call.function.name, args))
                print(f"-> {call.function.name}({json.dumps(args)})", flush=True)
                result = await mcp.call_tool(call.function.name, args)
                text = tool_result_to_text(result)
                print(
                    f"<- {'ERROR ' if result.is_error else ''}{text[:300]}{'...' if len(text) > 300 else ''}",
                    flush=True,
                )
                messages.append({"role": "tool", "tool_call_id": call.id, "content": text})
        run.answer = "Stopped: reached the maximum number of tool-calling steps."
        return run


def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Ask a question about the demo dataset via Groq + MCP.")
    parser.add_argument(
        "question",
        nargs="?",
        default="Which product category generated the most revenue from delivered orders, and how much?",
    )
    parser.add_argument("--model", default=os.getenv("GROQ_MODEL", DEFAULT_MODEL))
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "demo")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output")
    args = parser.parse_args()
    if not os.getenv("GROQ_API_KEY"):
        sys.exit("GROQ_API_KEY is not set (copy .env.example to .env and add your key).")
    print(f"Q: {args.question}\n(model: {args.model})\n", flush=True)
    run = asyncio.run(run_agent(args.question, args.model, args.data_dir, args.output_dir))
    print(f"\nA: {run.answer}")


if __name__ == "__main__":
    main()
