"""DuckDB storage: one local file holds filings, text chunks with embeddings, and metrics.

A single embedded analytical database keeps the stack small: DuckDB gives us SQL for the
financial metrics, full-text BM25 search (fts extension) and vector similarity
(array_cosine_similarity) without running a separate vector store.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import duckdb

from filings_copilot.edgar_client import Company, FilingRef
from filings_copilot.financial_facts_loader import MetricRow


@dataclass(frozen=True)
class ChunkRecord:
    chunk_id: str
    accession_number: str
    ticker: str
    fiscal_year: int
    section_item: str
    section_title: str
    position: int
    text: str
    embedding: list[float]


class FilingsStore:
    def __init__(self, path: Path | str, embedding_dimension: int, read_only: bool = False) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.dimension = embedding_dimension
        self.conn = duckdb.connect(str(path), read_only=read_only)
        self.conn.execute("INSTALL fts; LOAD fts;")
        if read_only:
            # Defense in depth behind sql_guard: the serving connection cannot write, and
            # cannot read local files or URLs even if a query slipped past validation.
            self.conn.execute("SET enable_external_access = false; SET lock_configuration = true;")
        else:
            self._create_schema()

    def close(self) -> None:
        self.conn.close()

    def cursor(self) -> duckdb.DuckDBPyConnection:
        """A per-call cursor; a single DuckDB connection must not be shared across threads."""
        return self.conn.cursor()

    def _create_schema(self) -> None:
        self.conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS companies (
                ticker VARCHAR PRIMARY KEY, cik INTEGER NOT NULL, name VARCHAR NOT NULL
            );
            CREATE TABLE IF NOT EXISTS filings (
                accession_number VARCHAR PRIMARY KEY,
                ticker VARCHAR NOT NULL, form VARCHAR NOT NULL, fiscal_year INTEGER NOT NULL,
                filing_date DATE NOT NULL, report_date DATE NOT NULL, url VARCHAR NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id VARCHAR PRIMARY KEY,
                accession_number VARCHAR NOT NULL, ticker VARCHAR NOT NULL,
                fiscal_year INTEGER NOT NULL, section_item VARCHAR NOT NULL,
                section_title VARCHAR NOT NULL, position INTEGER NOT NULL, text VARCHAR NOT NULL,
                embedding FLOAT[{self.dimension}] NOT NULL
            );
            CREATE TABLE IF NOT EXISTS annual_financials (
                ticker VARCHAR NOT NULL, metric VARCHAR NOT NULL, fiscal_year INTEGER NOT NULL,
                period_start DATE, period_end DATE NOT NULL, value DOUBLE NOT NULL,
                unit VARCHAR NOT NULL, xbrl_concept VARCHAR NOT NULL,
                accession_number VARCHAR NOT NULL, filed DATE NOT NULL,
                PRIMARY KEY (ticker, metric, fiscal_year)
            );
            """
        )

    def upsert_company(self, company: Company) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO companies VALUES (?, ?, ?)",
            [company.ticker, company.cik, company.name],
        )

    def upsert_filing(self, ref: FilingRef) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO filings VALUES (?, ?, ?, ?, ?, ?, ?)",
            [ref.accession_number, ref.ticker, ref.form, ref.fiscal_year,
             ref.filing_date, ref.report_date, ref.url],
        )

    def has_chunks(self, accession_number: str) -> bool:
        row = self.conn.execute(
            "SELECT count(*) FROM chunks WHERE accession_number = ?", [accession_number]
        ).fetchone()
        return bool(row and row[0])

    def replace_chunks(self, accession_number: str, chunks: Sequence[ChunkRecord]) -> None:
        self.conn.execute("DELETE FROM chunks WHERE accession_number = ?", [accession_number])
        self.conn.executemany(
            "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [[c.chunk_id, c.accession_number, c.ticker, c.fiscal_year, c.section_item,
              c.section_title, c.position, c.text, c.embedding] for c in chunks],
        )

    def replace_metrics(self, ticker: str, rows: Iterable[MetricRow]) -> int:
        self.conn.execute("DELETE FROM annual_financials WHERE ticker = ?", [ticker])
        values = [[r.ticker, r.metric, r.fiscal_year, r.period_start, r.period_end, r.value,
                   r.unit, r.concept, r.accession_number, r.filed] for r in rows]
        if values:
            self.conn.executemany(
                "INSERT INTO annual_financials VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values
            )
        return len(values)

    def rebuild_text_index(self) -> None:
        """DuckDB FTS indexes are static snapshots; rebuild after every ingest."""
        self.conn.execute(
            "PRAGMA create_fts_index('chunks', 'chunk_id', 'text', "
            "stemmer = 'porter', stopwords = 'english', overwrite = 1)"
        )
