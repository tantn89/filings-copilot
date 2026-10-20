"""End-to-end ingestion: EDGAR -> sections -> chunks + embeddings, and XBRL -> metrics."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field

from filings_copilot.edgar_client import EdgarClient
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
    report = IngestReport()
    for ticker in tickers:
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
            store.upsert_filing(ref)
            if not force and store.has_chunks(ref.accession_number):
                report.skipped_filings += 1
                continue
            sections = split_sections(html_to_text(edgar.filing_html(ref)))
            if not sections:
                report.warnings.append(f"{ref.ticker} FY{ref.fiscal_year}: no sections parsed")
                continue

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

    store.rebuild_text_index()
    return report
