# Strategy runner image. One-shot: each scheduled run starts a container, runs one command, exits.
# Build context is the tqqq-blend folder:  docker build -f deploy/Dockerfile .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=America/New_York
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY tqqq_blend ./tqqq_blend
COPY tests ./tests
COPY config*.toml ./

# state/ (ledgers, logs, data cache) is a volume mounted from the host
ENTRYPOINT ["python", "-m", "tqqq_blend"]
CMD ["signal"]
