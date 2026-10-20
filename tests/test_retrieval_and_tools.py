from __future__ import annotations

import json

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
