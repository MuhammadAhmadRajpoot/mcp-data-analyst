"""Reusable prompt templates exposed as MCP prompts."""

from __future__ import annotations

from .models import DatasetInfo


def _schema_block(info: DatasetInfo) -> str:
    cols = "\n".join(f"  - {c.name}: {c.type}" for c in info.columns)
    return f"Dataset `{info.name}` ({info.row_count} rows, from {info.source}):\n{cols}"


def exploratory_analysis(info: DatasetInfo, goal: str | None = None) -> str:
    focus = f"\nThe business question to keep in mind: {goal}\n" if goal else ""
    return f"""You are a careful data analyst. Perform an exploratory data analysis of the dataset below
using the mcp-data-analyst tools.

{_schema_block(info)}
{focus}
Work in this order:
1. Call `describe_dataset` on `{info.name}` to see null counts, cardinality and sample rows.
2. Call `profile` on `{info.name}` to get distributions, correlations and data-quality issues.
3. Use `query` (read-only DuckDB SQL) to answer 3-5 concrete questions the data invites, such as
   trends over time, top categories or segments, and relationships between numeric columns. You may join with
   other datasets returned by `list_datasets` when keys match.
4. Use `create_chart` for the one or two findings that are clearest as a picture.

Finish with a short report: key findings (each backed by a number from a query), caveats about data quality,
and suggested next questions. Do not invent numbers; only report what the tools returned."""


def data_quality_report(info: DatasetInfo) -> str:
    return f"""Produce a data-quality report for the dataset below using the mcp-data-analyst tools.

{_schema_block(info)}

Steps:
1. Call `profile` on `{info.name}` and review every reported issue (missing values, duplicates, outliers,
   constant or identifier-like columns).
2. For each issue, run a `query` that shows concrete examples (for example the duplicated rows, or the rows
   holding the most extreme outliers) so the problem can be verified.
3. Check referential integrity where a column looks like a foreign key to another dataset from
   `list_datasets` (for example ids that do not exist in the referenced table).

Report as a table with columns: issue, column, severity, evidence (numbers from the tools), suggested fix.
End with an overall readiness verdict for analysis. Only report what the tools returned."""
