FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MCP_DA_DATA_DIR=/data \
    MCP_DA_OUTPUT_DIR=/output \
    MCP_DA_TRANSPORT=streamable-http \
    MCP_DA_HOST=0.0.0.0 \
    MCP_DA_PORT=8106

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install . && useradd --create-home --uid 10001 analyst

# Demo data is baked in; mount your own read-only with: -v /path/to/data:/data:ro
COPY data/demo /data
RUN mkdir -p /output && chown analyst /output
USER analyst

EXPOSE 8106
ENTRYPOINT ["mcp-data-analyst"]
