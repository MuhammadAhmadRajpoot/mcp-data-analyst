"""Runtime configuration, read from ``MCP_DA_*`` environment variables and CLI flags."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Transport = Literal["stdio", "streamable-http"]


class Settings(BaseSettings):
    """All tunables of the server. Every field maps to ``MCP_DA_<FIELD_NAME>``."""

    model_config = SettingsConfigDict(env_prefix="MCP_DA_", extra="ignore")

    data_dir: Path = Field(default=Path("data/demo"), description="Sandboxed directory holding the datasets.")
    output_dir: Path = Field(default=Path("output"), description="Directory where charts and exports are written.")
    max_rows: int = Field(default=500, ge=1, le=100_000, description="Max rows returned by `query`.")
    max_export_rows: int = Field(
        default=100_000, ge=1, le=5_000_000, description="Max rows written by `export_result`."
    )
    max_profile_rows: int = Field(default=200_000, ge=100, description="Rows sampled by `profile` for large tables.")
    query_timeout_s: float = Field(default=10.0, gt=0, le=600, description="Per-query timeout in seconds.")
    max_file_size_mb: float = Field(default=100.0, gt=0, description="Files larger than this are not registered.")
    memory_limit: str = Field(default="1GB", description="DuckDB memory_limit setting.")
    transport: Transport = "stdio"
    host: str = "127.0.0.1"
    port: int = Field(default=8106, ge=1, le=65535)
    log_level: str = "INFO"

    @field_validator("data_dir", "output_dir")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @field_validator("memory_limit")
    @classmethod
    def _memory_limit(cls, value: str) -> str:
        # Interpolated into a SET statement, so keep it strictly to "<number><unit>".
        if not re.fullmatch(r"\d+(\.\d+)?\s*(KB|MB|GB|TB|KiB|MiB|GiB|TiB)", value.strip(), re.IGNORECASE):
            raise ValueError("memory_limit must look like '512MB' or '2GB'")
        return value.strip()

    @property
    def max_file_size_bytes(self) -> int:
        return int(self.max_file_size_mb * 1024 * 1024)
