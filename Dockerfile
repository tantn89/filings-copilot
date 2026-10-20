# Serves the API from a pre-built DuckDB file baked into the image (read-only at runtime).
# Build the data first:  filings-copilot ingest --tickers NVDA,MSFT,AAPL --years 2024-2025
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    FILINGS_DB_PATH=/app/data/filings.duckdb \
    HF_HOME=/app/.cache/huggingface

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . \
    # Pre-download the embedding model and the DuckDB fts extension so cold starts are fast.
    && python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')" \
    && python -c "import duckdb; duckdb.sql('INSTALL fts')"

COPY data/filings.duckdb ./data/filings.duckdb

RUN useradd --create-home app && chown -R app /app
USER app

EXPOSE 8080
CMD ["sh", "-c", "uvicorn filings_copilot.http_api:create_app --factory --host 0.0.0.0 --port ${PORT:-8080}"]
