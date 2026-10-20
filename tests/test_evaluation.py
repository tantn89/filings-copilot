from __future__ import annotations

from conftest import ScriptedMessagesAPI, fake_response, text_block, tool_use_block
from filings_copilot.agent_tools import ToolExecutor
from filings_copilot.evaluation import (
    ExtractedNumber,
    evaluate_agent,
    evaluate_retrieval,
    extract_numbers,
    number_matches,
)
from filings_copilot.research_agent import ResearchAgent


def test_extract_numbers_understands_scales_and_percent() -> None:
    found = extract_numbers(
        "Revenue was $130.5B (up 114%), or $130,497 million; EPS $2.94; margin 75.0 percent."
    )
    assert ExtractedNumber(130.5e9, "$") in found
    assert ExtractedNumber(114.0, "%") in found
    assert ExtractedNumber(130_497e6, "$") in found
    assert ExtractedNumber(2.94, "$") in found
    assert ExtractedNumber(75.0, "%") in found


def test_number_matching_respects_units_and_tolerance() -> None:
    found = extract_numbers("Gross margin was 75.0% on revenue of $130.5 billion.")
    assert number_matches({"value": 130.497e9, "rel_tol": 0.01}, found)
    assert number_matches({"value": 75.0, "unit": "%", "abs_tol": 0.3}, found)
    assert not number_matches({"value": 72.7, "unit": "%", "abs_tol": 0.3}, found)
    assert not number_matches({"value": 75.0, "rel_tol": 0.01}, found)  # % is not dollars


def test_retrieval_eval_reports_recall_and_mrr(tools: ToolExecutor) -> None:
    cases = [
        {"id": "a", "question": "export controls China", "expected_terms": ["export"],
         "expected_citations": [{"ticker": "NVDA", "section_item": "1A"}]},
        {"id": "b", "question": "TSMC wafers", "expected_terms": ["nonexistent-term"],
         "expected_citations": [{"ticker": "NVDA", "section_item": "1A"}]},
        {"id": "c", "question": "no citation expected", "kind": "numeric"},
    ]
    report = evaluate_retrieval(tools.retriever, cases, k=3)
    assert report["cases"] == 2
    assert report["filtered"] == {"recall@3": 0.5, "mrr": 0.5}


def test_agent_eval_grades_numbers_citations_and_abstention(tools: ToolExecutor) -> None:
    api = ScriptedMessagesAPI([
        fake_response([tool_use_block("t1", "search_filings", {
            "query": "gross margin", "tickers": ["NVDA"], "fiscal_years": [2025],
            "section_items": ["7"]})], "tool_use"),
        fake_response([text_block(
            "Gross margin rose to 75.0% on Data Center mix [NVDA-2025-7-0001].")], "end_turn"),
        fake_response([text_block("Tesla is not in the database.")], "end_turn"),
    ])
    cases = [
        {"id": "m", "kind": "mixed", "question": "Why did margin rise?",
         "expected_numbers": [{"value": 75.0, "unit": "%", "abs_tol": 0.3}],
         "expected_citations": [{"ticker": "NVDA", "section_item": "7"}],
         "expected_terms": ["Data Center"]},
        {"id": "u", "kind": "unanswerable", "question": "Tesla revenue?"},
    ]
    report = evaluate_agent(ResearchAgent(api.create, tools), cases)
    assert report["pass_rate"] == 1.0
    assert report["numeric_accuracy"] == 1.0
    assert report["citation_accuracy"] == 1.0
    assert report["abstention_accuracy"] == 1.0
    assert report["per_case"][0]["tools_used"] == ["search_filings"]
