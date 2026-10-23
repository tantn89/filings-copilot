"""End-to-end ingestion: EDGAR -> sections -> chunks + embeddings, and XBRL -> metrics."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field

from filings_copilot.edgar_client import EdgarClient, FilingRef
from filings_copilot.embeddings import Embedder
from filings_copilot.filings_store import ChunkRecord, FilingsStore
from filings_copilot.financial_facts_loader import annual_metrics
from filings_copilot.tenk_section_parser import html_to_text, split_sections
from filings_copilot.text_chunker import chunk_text

log = logging.getLogger(__name__)


@dataclass
class IngestReport:
    filings: int = 0
    skipped_filings: int = 0
    chunks: int = 0
    metric_rows: int = 0
    warnings: list[str] = field(default_factory=list)


def ingest(
    tickers: Iterable[str],
    fiscal_years: set[int],
    edgar: EdgarClient,
    store: FilingsStore,
    embedder: Embedder,
    chunk_size_words: int = 220,
    chunk_overlap_words: int = 40,
    force: bool = False,
) -> IngestReport:
    """Ingest what it can: a failing ticker or filing becomes a warning, not an abort, and
    the full-text index is always rebuilt so keyword search sees every stored chunk."""
    report = IngestReport()
    try:
        for ticker in tickers:
            try:
                _ingest_company(ticker, fiscal_years, edgar, store, embedder,
                                chunk_size_words, chunk_overlap_words, force, report)
            except Exception as e:  # network, EDGAR format or DB error for this ticker
                log.exception("ingest failed for %s", ticker)
                report.warnings.append(f"{ticker}: failed ({type(e).__name__}: {e})")
    finally:
        store.rebuild_text_index()
    return report


def _ingest_company(
    ticker: str,
    fiscal_years: set[int],
    edgar: EdgarClient,
    store: FilingsStore,
    embedder: Embedder,
    chunk_size_words: int,
    chunk_overlap_words: int,
    force: bool,
    report: IngestReport,
) -> None:
    company = edgar.company(ticker)
    store.upsert_company(company)
    report.metric_rows += store.replace_metrics(
        company.ticker, annual_metrics(edgar.company_facts(company), company.ticker)
    )

    refs = edgar.annual_reports(company, fiscal_years)
    missing = fiscal_years - {r.fiscal_year for r in refs}
    if missing:
        report.warnings.append(f"{company.ticker}: no 10-K found for {sorted(missing)}")

    for ref in refs:
        try:
            _ingest_filing(ref, edgar, store, embedder, chunk_size_words, chunk_overlap_words,
                           force, report)
        except Exception as e:
            log.exception("ingest failed for %s FY%s", ref.ticker, ref.fiscal_year)
            report.warnings.append(
                f"{ref.ticker} FY{ref.fiscal_year}: failed ({type(e).__name__}: {e})")


def _ingest_filing(
    ref: FilingRef,
    edgar: EdgarClient,
    store: FilingsStore,
    embedder: Embedder,
    chunk_size_words: int,
    chunk_overlap_words: int,
    force: bool,
    report: IngestReport,
) -> None:
    store.upsert_filing(ref)
    if not force and store.has_chunks(ref.accession_number):
        report.skipped_filings += 1
        return
    sections = split_sections(html_to_text(edgar.filing_html(ref)))
    if not sections:
        report.warnings.append(f"{ref.ticker} FY{ref.fiscal_year}: no sections parsed")
        return

    texts, meta = [], []
    for section in sections:
        for position, text in enumerate(
            chunk_text(section.text, chunk_size_words, chunk_overlap_words)
        ):
            texts.append(text)
            meta.append((section, position))
    vectors = embedder.embed_documents(texts)
    records = [
        ChunkRecord(
            chunk_id=f"{ref.ticker}-{ref.fiscal_year}-{section.item}-{position:04d}",
            accession_number=ref.accession_number,
            ticker=ref.ticker,
            fiscal_year=ref.fiscal_year,
            section_item=section.item,
            section_title=section.title,
            position=position,
            text=text,
            embedding=vector,
        )
        for text, (section, position), vector in zip(texts, meta, vectors, strict=True)
    ]
    store.replace_chunks(ref.accession_number, records)
    report.filings += 1
    report.chunks += len(records)
    log.info("%s FY%s: %d sections, %d chunks",
             ref.ticker, ref.fiscal_year, len(sections), len(records))
