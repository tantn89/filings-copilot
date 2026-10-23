# Serves the API from a pre-built DuckDB file baked into the image (read-only at runtime).
# Build the data first:  filings-copilot ingest --tickers NVDA,MSFT,AAPL --years 2024-2025
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    FILINGS_DB_PATH=/app/data/filings.duckdb \
    HF_HOME=/app/.cache/huggingface \
    HF_HUB_DISABLE_TELEMETRY=1

# Run as an unprivileged user; the app only reads its baked-in data at runtime.
RUN useradd --create-home --uid 1000 app
WORKDIR /app

COPY pyproject.toml README.md constraints.txt ./
COPY src ./src
RUN pip install --no-cache-dir -c constraints.txt . && chown -R app /app

USER app
# Fetch the embedding model and DuckDB fts extension as the runtime user, so the container
# starts without network downloads and the files land in that user's cache.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')" \
    && python -c "import duckdb; duckdb.sql('INSTALL fts')"

COPY --chown=app data/filings.duckdb ./data/filings.duckdb

EXPOSE 8080
CMD ["sh", "-c", "uvicorn filings_copilot.http_api:create_app --factory --host 0.0.0.0 --port ${PORT:-8080}"]
