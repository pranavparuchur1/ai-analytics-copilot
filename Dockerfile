FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.22 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY configs ./configs
COPY infrastructure/postgres/01_schema.sql ./infrastructure/postgres/01_schema.sql
RUN uv sync --frozen --no-dev

RUN useradd --create-home app && mkdir -p /app/data && chown app /app/data
USER app
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8765/api/health')"
CMD ["uv", "run", "--no-sync", "acp", "serve", "--host", "0.0.0.0", "--port", "8765"]
