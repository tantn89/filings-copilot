from __future__ import annotations

import json
import time

import pytest

from filings_copilot import agent_tools
from filings_copilot.agent_tools import ToolExecutor
from filings_copilot.hybrid_retriever import SearchFilters, reciprocal_rank_fusion


def test_rrf_rewards_documents_ranked_well_by_both_lists() -> None:
    scores = reciprocal_rank_fusion([["a", "b", "c"], ["b", "c", "a"]])
    assert max(scores, key=scores.__getitem__) == "b"


def test_search_finds_relevant_passage(tools: ToolExecutor) -> None:
    hits = tools.retriever.search("export controls China")
    assert hits[0].section_item == "1A"
    assert "Export controls" in hits[0].text
    assert hits[0].url.endswith("nvda-20250126.htm")


def test_search_filters_by_section(tools: ToolExecutor) -> None:
    hits = tools.retriever.search("revenue", SearchFilters(section_items=["7"]))
    assert hits and {h.section_item for h in hits} == {"7"}


def test_search_tool_returns_citation_ids(tools: ToolExecutor) -> None:
    outcome = tools.run("search_filings", {
        "query": "TSMC supply risk", "tickers": ["nvda"], "fiscal_years": [2025],
        "section_items": [],
    })
    assert not outcome.is_error
    assert "[NVDA-2025-1A-0003]" in outcome.content
    assert outcome.citations


def test_query_financials_runs_guarded_sql(tools: ToolExecutor) -> None:
    outcome = tools.run("query_financials", {"sql": """
        SELECT fiscal_year, round(value / lag(value) OVER (ORDER BY fiscal_year) - 1, 3) AS growth
        FROM annual_financials WHERE ticker = 'NVDA' AND metric = 'revenue' ORDER BY fiscal_year
    """})
    rows = json.loads(outcome.content)["rows"]
    assert rows[-1] == {"fiscal_year": 2025, "growth": 1.142}


def test_query_financials_reports_rejection_as_tool_error(tools: ToolExecutor) -> None:
    outcome = tools.run("query_financials", {"sql": "DROP TABLE companies"})
    assert outcome.is_error
    assert "only select" in outcome.content.lower()


def test_list_companies(tools: ToolExecutor) -> None:
    data = json.loads(tools.run("list_companies", {}).content)
    assert data == [{"ticker": "NVDA", "name": "NVIDIA CORP", "fiscal_years_with_10k_text": [2025]}]


def test_unknown_tool_is_an_error(tools: ToolExecutor) -> None:
    assert tools.run("rm_rf", {}).is_error


def test_database_errors_go_back_to_the_model(tools: ToolExecutor) -> None:
    # A wrong column name is the most common model mistake; it must not abort the run.
    outcome = tools.run("query_financials", {"sql": "SELECT revenue FROM annual_financials"})
    assert outcome.is_error
    assert "revenue" in outcome.content


def test_large_limits_are_capped_and_output_is_bounded(tools: ToolExecutor) -> None:
    outcome = tools.run("query_financials", {
        "sql": "SELECT * FROM annual_financials a, annual_financials b LIMIT 100000"})
    rows = json.loads(outcome.content)["rows"]
    assert len(rows) == 4  # 2 x 2 rows in the fixture, well under the cap
    assert len(outcome.content) <= agent_tools.MAX_TOOL_OUTPUT_CHARS + 100


def test_tool_output_is_truncated(tools: ToolExecutor, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_tools, "MAX_TOOL_OUTPUT_CHARS", 50)
    outcome = tools.run("search_filings", {
        "query": "revenue", "tickers": [], "fiscal_years": [], "section_items": []})
    assert outcome.content.endswith("narrow the query]")
    assert len(outcome.content) < 120


def test_scalar_filters_from_unvalidated_models_are_accepted(tools: ToolExecutor) -> None:
    outcome = tools.run("search_filings", {
        "query": "export controls", "tickers": "NVDA", "fiscal_years": 2025,
        "section_items": None})
    assert not outcome.is_error and "[NVDA-2025-1A-0002]" in outcome.content


def test_slow_queries_are_interrupted(tools: ToolExecutor, monkeypatch: pytest.MonkeyPatch) -> None:
    # Bypass the guard to exercise the watchdog itself with a CPU-heavy, low-memory query.
    monkeypatch.setattr(agent_tools, "validate_select", lambda sql: sql)
    monkeypatch.setattr(agent_tools, "QUERY_TIMEOUT_S", 0.2)
    started = time.perf_counter()
    outcome = tools.run("query_financials", {"sql": "SELECT count(*) FROM range(5000000000)"})
    assert outcome.is_error and "interrupt" in outcome.content.lower()
    assert time.perf_counter() - started < 5
