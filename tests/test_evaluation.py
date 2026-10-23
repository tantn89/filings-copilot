from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import ScriptedMessagesAPI, fake_response, text_block, tool_use_block
from filings_copilot.agent_tools import ToolExecutor
from filings_copilot.evaluation import (
    ExtractedNumber,
    comparison_table,
    estimate_cost,
    evaluate_agent,
    evaluate_retrieval,
    extract_numbers,
    grade,
    load_latest_agent_reports,
    number_matches,
)
from filings_copilot.research_agent import AgentResult, ResearchAgent


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


def test_comparison_table_uses_latest_report_per_model(tmp_path: Path) -> None:
    def write(name: str, model: str, pass_rate: float) -> None:
        agent = {"pass_rate": pass_rate, "numeric_accuracy": 1.0, "citation_accuracy": 0.5,
                 "abstention_accuracy": None, "latency_p50_s": 12.34, "avg_cost_usd": 0.0123}
        (tmp_path / name).write_text(json.dumps(
            {"provider": "openrouter", "model": model, "agent": agent}))

    write("eval-20261020-100000.json", "a/one", 0.5)
    write("eval-20261020-110000.json", "a/one", 0.9)   # newer run of the same model wins
    write("eval-20261020-120000.json", "b/two", 0.7)
    retrieval_only = {"model": "x"}
    (tmp_path / "eval-20261020-130000.json").write_text(json.dumps(retrieval_only))

    table = comparison_table(load_latest_agent_reports(tmp_path)).splitlines()
    assert len(table) == 4
    assert table[2].startswith("| a/one | 90% | 100% | 50% | n/a | 12.3s | $0.0123 |")
    assert table[3].startswith("| b/two | 70%")


@pytest.mark.parametrize("answer", [
    # Real abstentions from Claude Opus 5.5 / Sonnet 5.5 that an earlier phrase list missed.
    "I can't tell you what NVIDIA's FY2028 revenue will be. My data covers only reported "
    "results, so any number I gave would be a guess.",
    "I can't give you Tesla's fiscal 2025 revenue because the database has no Tesla data.",
    "Tesla isn't in the database.",
])
def test_abstention_phrasings_are_recognised(answer: str) -> None:
    assert grade({"kind": "unanswerable"}, AgentResult(answer, [], [])) == {"abstained": True}


def test_a_confident_answer_is_not_an_abstention() -> None:
    result = AgentResult("Tesla's revenue was $97.7B in fiscal 2025.", [], [])
    assert grade({"kind": "unanswerable"}, result) == {"abstained": False}


def test_refusal_that_still_invents_a_figure_fails() -> None:
    case = {"kind": "unanswerable", "forbidden_patterns": [r"\$\s?\d"]}
    lying = AgentResult("Tesla is not in my database, but its revenue was about $97.7B.", [], [])
    honest = AgentResult("Tesla is not in the database, so I can't give that figure.", [], [])
    assert grade(case, lying) == {"no_forbidden": False, "abstained": True}
    assert grade(case, honest) == {"no_forbidden": True, "abstained": True}


def test_refusal_must_lead_the_answer() -> None:
    buried = AgentResult(
        "NVIDIA will grow fast. Revenue should triple. Of course I cannot predict it.", [], [])
    assert grade({"kind": "unanswerable"}, buried) == {"abstained": False}


def test_lead_terms_must_be_in_the_first_sentence() -> None:
    case = {"kind": "numeric", "lead_terms": ["NVIDIA"]}
    wrong = AgentResult("Microsoft had the highest margin at 69.8%. NVIDIA was 75.0%.", [], [])
    right = AgentResult("NVIDIA had the highest margin at 75.0%. Microsoft was 69.8%.", [], [])
    assert grade(case, wrong)["lead"] is False
    assert grade(case, right)["lead"] is True


def test_cost_estimate_prices_cache_tokens_and_skips_unknown_models() -> None:
    result = AgentResult("ok", [], [], input_tokens=1_000_000, output_tokens=100_000,
                         cache_read_tokens=600_000, cache_write_tokens=100_000)
    # uncached 300k x $2 + writes 100k x $2.5 + reads 600k x $0.2 + output 100k x $10
    assert estimate_cost("claude-sonnet-5-5", result) == pytest.approx(0.6 + 0.25 + 0.12 + 1.0)
    assert estimate_cost("some/unknown-model", result) is None
    assert estimate_cost("x", AgentResult("ok", [], [], cost_usd=0.5)) == 0.5
