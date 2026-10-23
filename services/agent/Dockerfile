# Build context is the repository root (compose.yaml), so the image can carry the offline
# sample and the Zetaris SQL that sample mode and bootstrap read (BACKLOG B-1). The repo
# layout is mirrored under /app so the package's repo-relative defaults also resolve.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
WORKDIR /app/services/agent
ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    SAMPLE_DATA_DIR=/app/data/sample ZETARIS_SQL_DIR=/app/zetaris/sql LOGS_DIR=/app/logs
COPY services/agent/pyproject.toml services/agent/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY services/agent/src ./src
RUN uv sync --frozen --no-dev
COPY data/sample /app/data/sample
COPY zetaris/sql /app/zetaris/sql
COPY catalog /app/catalog
COPY input_examples /app/input_examples
RUN mkdir -p /app/logs
EXPOSE 8000
CMD ["/app/services/agent/.venv/bin/python", "-m", "tiresias_agent.app"]
