# Sleeves runner image. One-shot: each scheduled run starts a container, runs one command, exits.
# Build context is the sleeves folder:  docker build -f deploy/Dockerfile .
# The server, gateways and timers live in tqqq-blend/deploy; this image runs next to tqqq-blend's.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=America/New_York \
    UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PATH=/app/.venv/bin:$PATH
WORKDIR /app

# Exactly the versions CI tested: uv.lock, runtime dependencies only
COPY --from=ghcr.io/astral-sh/uv:0.8.14 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-cache

COPY sleeves ./sleeves
COPY config*.toml ./

# state/ (ledgers, logs, data cache) is a volume mounted from the host, shared with tqqq-blend's buckets
ENTRYPOINT ["python", "-m", "sleeves"]
CMD ["signal"]
