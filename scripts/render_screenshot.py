"""Build ``output/screenshot.html`` from a real agent transcript and the chart it produced.

Steps (see README, "Screenshot"):
1. python examples/groq_agent.py "..." > docs/transcript.txt
2. python scripts/render_screenshot.py
3. npx -y playwright screenshot --channel msedge --viewport-size=1440,690 \
       file:///<repo>/output/screenshot.html docs/screenshot.png
"""

from __future__ import annotations

import base64
import html
import re
from pathlib import Path

from mcp_data_analyst.analyst import Analyst
from mcp_data_analyst.config import Settings

ROOT = Path(__file__).resolve().parents[1]
MAX_LINE = 150


def _line_class(line: str) -> str:
    if line.startswith("Q:"):
        return "q"
    if line.startswith("->"):
        return "call"
    if line.startswith("<-"):
        return "result"
    if line.startswith("A:"):
        return "answer"
    return ""


def render(transcript: str, chart_png: bytes, issues: list[str]) -> str:
    transcript = re.sub(r"[A-Z]:\\\\[^\"]*?\\\\output\\\\", r"output\\\\", transcript)
    rows = []
    for raw in transcript.splitlines():
        line = raw if len(raw) <= MAX_LINE else raw[: MAX_LINE - 3] + "..."
        rows.append(f'<div class="{_line_class(line)}">{html.escape(line) or "&nbsp;"}</div>')
    chart = base64.b64encode(chart_png).decode()
    findings = "".join(f"<li>{html.escape(i)}</li>" for i in issues)
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
body {{ margin:0; background:#f0efec; font-family:'Segoe UI',system-ui,sans-serif; color:#0b0b0b; }}
header {{ padding:22px 32px 6px; }} h1 {{ margin:0; font-size:24px; }}
header p {{ margin:4px 0 0; color:#52514e; font-size:14px; }}
main {{ display:grid; grid-template-columns: 1fr 1fr; gap:20px; padding:16px 32px 32px; }}
.term {{ background:#1a1a19; color:#c3c2b7; border-radius:10px; padding:16px 18px; font:13.5px/1.55 Consolas,monospace;
  overflow:hidden; white-space:pre-wrap; word-break:break-all; }}
.bar {{ color:#8b8a85; margin-bottom:10px; }} .q {{ color:#f0efec; font-weight:600; }} .call {{ color:#6fb0ff; }}
.result {{ color:#9a998f; }} .answer {{ color:#f0efec; }}
.card {{ background:#fcfcfb; border-radius:10px; padding:14px; box-shadow:0 1px 3px rgba(0,0,0,.08); }}
.card img {{ width:100%; display:block; }} .card h2 {{ font-size:15px; margin:2px 4px 8px; }}
.card ul {{ margin:0; padding-left:22px; font-size:13.5px; line-height:1.6; color:#2b2a28; }}
code {{ font-family:Consolas,monospace; }} .cap {{ color:#52514e; font-size:13px; margin:10px 4px 16px; }}
</style></head><body>
<header><h1>mcp-data-analyst</h1>
<p>Real session: Groq <code>openai/gpt-oss-120b</code> agent &rarr; MCP (stdio) &rarr;
read-only DuckDB over the demo data</p>
</header>
<main><div class="term"><div class="bar">$ python examples/groq_agent.py</div>{"".join(rows)}</div>
<div><div class="card"><img src="data:image/png;base64,{chart}" alt="chart"></div>
<p class="cap">Chart returned by the <code>create_chart</code> tool as MCP image content
(and saved to output/monthly_revenue.png).</p>
<div class="card"><h2>profile(order_items) &mdash; data-quality issues found</h2><ul>{findings}</ul></div>
</div></main></body></html>"""


def main() -> None:
    transcript = (ROOT / "docs" / "transcript.txt").read_text(encoding="utf-8")
    chart = (ROOT / "output" / "monthly_revenue.png").read_bytes()
    analyst = Analyst(Settings(data_dir=ROOT / "data" / "demo", output_dir=ROOT / "output"))
    try:
        profile = analyst.profile("order_items")
    finally:
        analyst.close()
    issues = [f"[{i.severity}] {i.kind}{f' ({i.column})' if i.column else ''}: {i.detail}" for i in profile.issues]
    target = ROOT / "output" / "screenshot.html"
    target.write_text(render(transcript, chart, issues), encoding="utf-8")
    print(target)


if __name__ == "__main__":
    main()
