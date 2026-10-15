"""Command-line entry point: ``mcp-data-analyst [--data-dir DIR] [--transport stdio|streamable-http] ...``."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence

from pydantic import ValidationError

from . import __version__
from .analyst import Analyst
from .config import Settings
from .errors import AnalystError
from .logging_config import configure_logging
from .server import create_server

log = logging.getLogger(__name__)

# CLI flag -> Settings field. Flags override MCP_DA_* environment variables.
_FLAGS: dict[str, tuple[type, str]] = {
    "data_dir": (str, "Directory with CSV/TSV, Parquet, XLSX and SQLite files (env MCP_DA_DATA_DIR)."),
    "output_dir": (str, "Where charts and exports are written (env MCP_DA_OUTPUT_DIR)."),
    "max_rows": (int, "Max rows returned by `query` (env MCP_DA_MAX_ROWS)."),
    "max_export_rows": (int, "Max rows written by `export_result` (env MCP_DA_MAX_EXPORT_ROWS)."),
    "query_timeout_s": (float, "Query timeout in seconds (env MCP_DA_QUERY_TIMEOUT_S)."),
    "max_file_size_mb": (float, "Skip files larger than this (env MCP_DA_MAX_FILE_SIZE_MB)."),
    "memory_limit": (str, "DuckDB memory limit, e.g. 1GB (env MCP_DA_MEMORY_LIMIT)."),
    "host": (str, "Bind host for streamable-http (env MCP_DA_HOST)."),
    "port": (int, "Port for streamable-http (env MCP_DA_PORT, default 8106)."),
    "log_level": (str, "DEBUG, INFO, WARNING or ERROR (env MCP_DA_LOG_LEVEL)."),
}


def parse_settings(argv: Sequence[str] | None = None) -> Settings:
    parser = argparse.ArgumentParser(prog="mcp-data-analyst", description="Read-only data-analysis MCP server.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], help="MCP transport (default stdio).")
    for field, (kind, help_text) in _FLAGS.items():
        parser.add_argument(f"--{field.replace('_', '-')}", dest=field, type=kind, help=help_text)
    args = parser.parse_args(argv)
    overrides = {k: v for k, v in vars(args).items() if v is not None}
    try:
        return Settings(**overrides)
    except ValidationError as exc:
        parser.error(str(exc))


def main(argv: Sequence[str] | None = None) -> None:
    settings = parse_settings(argv)
    configure_logging(settings.log_level)
    try:
        analyst = Analyst(settings)
    except AnalystError as exc:
        log.error("cannot start server", extra={"error": str(exc)})
        raise SystemExit(2) from exc
    server = create_server(analyst)
    log.info(
        "starting server",
        extra={
            "transport": settings.transport,
            "datasets": len(analyst.engine.datasets),
            "data_dir": str(settings.data_dir),
        },
    )
    try:
        if settings.transport == "streamable-http":
            server.run("streamable-http", host=settings.host, port=settings.port)
        else:
            server.run("stdio")
    except KeyboardInterrupt:
        pass
    finally:
        analyst.close()
