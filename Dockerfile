FROM python:3.14-slim

RUN groupadd -r restbench && useradd -r -g restbench restbench

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/
COPY data/ data/

RUN pip install --no-cache-dir . && \
    mkdir -p /app/persist && chown restbench:restbench /app/persist

USER restbench

EXPOSE 8001

ENV RESTBENCH_PROJECT_DIR=/app \
    RESTBENCH_PORT=8001 \
    RESTBENCH_DATA_DIR=/app/persist \
    RESTBENCH_PERSIST=true \
    RESTBENCH_MAX_CONCURRENT=5 \
    RESTBENCH_MAX_GAMES_PER_HOUR=60 \
    RESTBENCH_GAME_EXPIRY=7200 \
    RESTBENCH_LOG_LEVEL=INFO

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; r=httpx.get('http://localhost:8001/health'); assert r.status_code==200"

CMD ["uvicorn", "restbench.api.server:app", "--host", "0.0.0.0", "--port", "8001"]
