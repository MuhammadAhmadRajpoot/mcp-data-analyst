"""MCP surface: tools, resources and prompts registered on an ``MCPServer`` (the SDK's FastMCP)."""

from __future__ import annotations

import json
from typing import Annotated, Literal

import anyio
from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ResourceNotFoundError
from mcp_types import ImageContent, TextContent, ToolAnnotations
from pydantic import Field

from . import __version__, prompts
from .analyst import Analyst
from .charts import ChartType
from .errors import DatasetNotFoundError
from .models import DatasetDescription, DatasetList, DatasetProfile, ExportResult, QueryResult

INSTRUCTIONS = """Read-only data analysis over local CSV, Excel, Parquet and SQLite files.
Start with `list_datasets`, inspect a table with `describe_dataset` or `profile`, then answer questions with
`query` (DuckDB SQL dialect, SELECT only, registered table names only). Use `create_chart` to visualise and
`export_result` to save a result set. Results are row-limited; check the `truncated` flag."""

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
# Charts/exports only create new files inside the sandboxed output directory.
WRITES_OUTPUT = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
)

DatasetName = Annotated[str, Field(description="Dataset (table) name as returned by list_datasets.")]
Sql = Annotated[str, Field(description="A single read-only SELECT statement in DuckDB SQL.")]
OutputName = Annotated[
    str | None,
    Field(description="Optional bare file name (no directories) for the output dir; auto-generated if omitted."),
]


def create_server(analyst: Analyst) -> MCPServer:
    mcp = MCPServer(
        "mcp-data-analyst",
        title="MCP Data Analyst",
        instructions=INSTRUCTIONS,
        version=__version__,
        log_level=analyst.settings.log_level.upper(),  # type: ignore[arg-type]
    )

    # -- tools ---------------------------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY)
    async def list_datasets() -> DatasetList:
        """List every registered dataset with its source file, row count and column schema."""
        return analyst.list_datasets()

    @mcp.tool(annotations=READ_ONLY)
    async def describe_dataset(
        name: DatasetName,
        sample_rows: Annotated[int, Field(ge=0, le=50, description="Number of sample rows to include.")] = 5,
    ) -> DatasetDescription:
        """Show a dataset's columns, DuckDB types, null counts, distinct counts and sample rows."""
        return await anyio.to_thread.run_sync(analyst.describe, name, sample_rows)

    @mcp.tool(annotations=READ_ONLY)
    async def query(
        sql: Sql,
        max_rows: Annotated[int | None, Field(ge=1, description="Row cap (cannot exceed the server limit).")] = None,
    ) -> QueryResult:
        """Run a read-only SQL query (DuckDB dialect) over the registered datasets.

        Joins, CTEs, window functions and aggregates are supported. DDL, DML, COPY, ATTACH, PRAGMA,
        file-reading functions and multiple statements are rejected.
        """
        return await anyio.to_thread.run_sync(analyst.query, sql, max_rows)

    @mcp.tool(annotations=READ_ONLY)
    async def profile(
        name: DatasetName,
        columns: Annotated[list[str] | None, Field(description="Restrict to these columns (default: all).")] = None,
    ) -> DatasetProfile:
        """Profile a dataset: summary stats, histograms, top values, correlations and data-quality issues
        (duplicates, missing values, IQR outliers, constant and identifier-like columns)."""
        return await anyio.to_thread.run_sync(analyst.profile, name, columns)

    @mcp.tool(annotations=WRITES_OUTPUT)
    async def create_chart(
        sql: Annotated[str, Field(description="Read-only SELECT producing the data to plot.")],
        chart_type: Annotated[ChartType, Field(description="bar, line, scatter or histogram.")],
        x: Annotated[str, Field(description="Column for the x axis (the value column for histograms).")],
        y: Annotated[str | None, Field(description="Numeric column for the y axis. Optional for bar (counts).")] = None,
        title: Annotated[str | None, Field(description="Chart title.")] = None,
        bins: Annotated[int, Field(ge=2, le=200, description="Histogram bin count.")] = 20,
        filename: OutputName = None,
    ) -> list[TextContent | ImageContent]:
        """Plot a query result with matplotlib. Returns the PNG as image content and saves it to the output dir."""
        png, path, rows, truncated = await anyio.to_thread.run_sync(
            lambda: analyst.create_chart(sql, chart_type, x, y, title, bins, filename)
        )
        summary = {"saved_to": str(path), "rows_plotted": rows, "truncated": truncated}
        return [TextContent(type="text", text=json.dumps(summary)), Image(data=png, format="png").to_image_content()]

    @mcp.tool(annotations=WRITES_OUTPUT)
    async def export_result(
        sql: Sql,
        format: Annotated[Literal["csv", "xlsx"], Field(description="Output file format.")] = "csv",
        filename: OutputName = None,
    ) -> ExportResult:
        """Run a read-only query and write the full result (up to the export row limit) to CSV or Excel."""
        return await anyio.to_thread.run_sync(analyst.export, sql, format, filename)

    # -- resources -----------------------------------------------------------------------

    @mcp.resource("dataset://catalog", name="catalog", mime_type="application/json")
    def catalog() -> str:
        """All registered datasets and their schemas."""
        return analyst.list_datasets().model_dump_json(indent=2)

    @mcp.resource("dataset://{name}/schema", name="dataset_schema", mime_type="application/json")
    def dataset_schema(name: str) -> str:
        """Column names and types, row count and source file of one dataset."""
        try:
            return analyst.schema(name).model_dump_json(indent=2)
        except DatasetNotFoundError as exc:
            raise ResourceNotFoundError(str(exc)) from exc

    # -- prompts -------------------------------------------------------------------------

    @mcp.prompt(title="Exploratory analysis")
    def exploratory_analysis(dataset: str, goal: str | None = None) -> str:
        """Step-by-step EDA plan for a dataset, optionally focused on a business question."""
        return prompts.exploratory_analysis(analyst.schema(dataset), goal)

    @mcp.prompt(title="Data quality report")
    def data_quality_report(dataset: str) -> str:
        """Checklist-driven data-quality audit of a dataset, with evidence for every issue."""
        return prompts.data_quality_report(analyst.schema(dataset))

    return mcp
