"""Ingest end to end against a mocked EDGAR: filing HTML and XBRL facts land in DuckDB."""

from __future__ import annotations

import duckdb
import httpx
import pytest

from filings_copilot.edgar_client import EdgarClient
from filings_copilot.embeddings import HashingEmbedder
from filings_copilot.filings_store import ChunkRecord, FilingsStore
from filings_copilot.hybrid_retriever import HybridRetriever
from filings_copilot.ingest_pipeline import ingest
from test_edgar_client import SUBMISSIONS
from test_text_processing import make_10k_html

COMPANY_FACTS = {"cik": 1045810, "facts": {"us-gaap": {"Revenues": {"units": {"USD": [
    {"val": 130497e6, "start": "2024-01-29", "end": "2025-01-26", "filed": "2025-02-26",
     "form": "10-K", "accn": "0001-25-1"},
]}}}}}


def edgar_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("company_tickers.json"):
        return httpx.Response(200, json={"0": {"cik_str": 1045810, "ticker": "NVDA",
                                               "title": "NVIDIA CORP"}})
    if "submissions" in path:
        return httpx.Response(200, json=SUBMISSIONS)
    if "companyfacts" in path:
        return httpx.Response(200, json=COMPANY_FACTS)
    if path.endswith(".htm"):
        return httpx.Response(200, text=make_10k_html())
    return httpx.Response(404)


def test_ingest_end_to_end_is_idempotent() -> None:
    embedder = HashingEmbedder(64)
    store = FilingsStore(":memory:", embedder.dimension)
    edgar = EdgarClient("t t@example.com", 1000, transport=httpx.MockTransport(edgar_handler))

    report = ingest(["nvda"], {2025, 2023}, edgar, store, embedder, 120, 20)
    assert report.filings == 1 and report.chunks > 0 and report.metric_rows == 1
    assert report.warnings == ["NVDA: no 10-K found for [2023]"]

    hits = HybridRetriever(store, embedder).search("export controls")
    assert hits[0].ticker == "NVDA" and hits[0].section_item == "1A"
    assert hits[0].url.endswith("/a.htm")

    again = ingest(["NVDA"], {2025}, edgar, store, embedder, 120, 20)
    assert again.filings == 0 and again.skipped_filings == 1
    count = store.cursor().execute("SELECT count(*) FROM chunks").fetchone()
    assert count is not None and count[0] == report.chunks


def test_one_failing_filing_does_not_stop_the_ingest() -> None:
    def flaky(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/d.htm"):  # the FY2024 10-K document
            return httpx.Response(404)
        return edgar_handler(request)

    embedder = HashingEmbedder(64)
    store = FilingsStore(":memory:", embedder.dimension)
    edgar = EdgarClient("t t@example.com", 1000, transport=httpx.MockTransport(flaky))

    report = ingest(["NVDA"], {2024, 2025}, edgar, store, embedder, 120, 20)
    assert report.filings == 1
    assert any("FY2024: failed" in w for w in report.warnings)
    # The index was rebuilt, so the filing that did load is searchable.
    assert HybridRetriever(store, embedder).search("export controls")


def test_failed_replace_keeps_the_previous_chunks() -> None:
    embedder = HashingEmbedder(8)
    store = FilingsStore(":memory:", embedder.dimension)
    vec = embedder.embed_query("x")
    good = ChunkRecord("A-2025-7-0000", "acc", "A", 2025, "7", "MD&A", 0, "old text", vec)
    store.replace_chunks("acc", [good])
    duplicate_ids = [good, good]  # violates the primary key mid-insert
    with pytest.raises(duckdb.Error):
        store.replace_chunks("acc", duplicate_ids)
    rows = store.cursor().execute("SELECT text FROM chunks").fetchall()
    assert rows == [("old text",)]
