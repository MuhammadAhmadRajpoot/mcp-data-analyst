"""Domain errors. Their messages are safe to show to the MCP client verbatim.

They subclass the SDK's ``ToolError`` so MCPServer returns them as ``isError`` tool
results with the message intact, while any *other* exception is reported only as a
generic "Error executing tool" (no internals leak to the client).
"""

from __future__ import annotations

from mcp.server.mcpserver.exceptions import ToolError


class AnalystError(ToolError):
    """Base class for every error that is reported back to the client as a tool error."""


class SecurityError(AnalystError):
    """A request tried to escape the sandbox or run a forbidden SQL construct."""


class DatasetNotFoundError(AnalystError):
    """The requested dataset name is not registered."""


class QueryError(AnalystError):
    """DuckDB rejected the query (syntax, binder or runtime error)."""


class QueryTimeoutError(QueryError):
    """The query ran longer than the configured timeout and was interrupted."""


class InvalidArgumentError(AnalystError):
    """A tool argument is invalid (unknown column, bad chart type, ...)."""
