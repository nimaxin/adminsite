# The online demo: the example shop behind uvicorn, reset every hour.
# Built from the repository root, as demo/compose.yaml does:
#   docker build -f demo/Dockerfile .
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# The dependencies first, so a change to the code does not install them again.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --locked --no-dev --group demo --no-install-project

COPY src ./src
COPY examples ./examples
COPY demo ./demo
RUN uv sync --locked --no-dev --group demo --no-editable

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin demo
USER demo

ENV PATH="/app/.venv/bin:$PATH" \
    DEMO_DATA=/data

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"]

# Only Caddy can reach the app, so the address it forwards for is trusted.
CMD ["uvicorn", "demo.app:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]
