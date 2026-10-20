# Strategy runner image. One-shot: each scheduled run starts a container, runs one command, exits.
# Build context is the tqqq-blend folder:  docker build -f deploy/Dockerfile .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=America/New_York \
    UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PATH=/app/.venv/bin:$PATH
WORKDIR /app

# Exactly the versions CI tested: uv.lock, runtime dependencies only
COPY --from=ghcr.io/astral-sh/uv:0.8.14 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-cache

COPY tqqq_blend ./tqqq_blend
COPY config*.toml ./

# state/ (ledgers, logs, data cache) is a volume mounted from the host
ENTRYPOINT ["python", "-m", "tqqq_blend"]
CMD ["signal"]
