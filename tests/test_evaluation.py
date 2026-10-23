from __future__ import annotations

from pathlib import Path

import pytest
from regressgate import Case, TargetResult, load_cases
from regressgate.checks import run_checks

from conftest import ScriptedMessagesAPI, fake_response, text_block, tool_use_block
from filings_copilot.agent_tools import ToolExecutor
from filings_copilot.evaluation import (
    comparison_table,
    estimate_cost,
    evaluate_agent,
    evaluate_retrieval,
    load_latest_agent_reports,
)
from filings_copilot.research_agent import AgentResult, ResearchAgent

GOLDEN = {c.id: c for c in load_cases(Path(__file__).parents[1] / "evals" / "golden_cases.jsonl")}


def passes(case_id: str, answer: str) -> dict[str, bool]:
    """Check outcomes of one golden case for a given answer text."""
    return {k: v.passed for k, v in run_checks(GOLDEN[case_id], TargetResult(answer)).items()}


def case(cid: str, question: str, checks: list[dict[str, object]], kind: str) -> Case:
    return Case.model_validate({"id": cid, "input": question, "checks": checks, "tags": [kind]})


def test_retrieval_eval_reports_recall_and_mrr(tools: ToolExecutor) -> None:
    citation = {"type": "citation", "any_of": [{"ticker": "NVDA", "section_item": "1A"}]}
    cases = [
        case("a", "export controls China",
             [citation, {"type": "contains", "terms": ["export"]}], "narrative"),
        case("b", "TSMC wafers",
             [citation, {"type": "contains", "terms": ["nonexistent-term"]}], "narrative"),
        case("c", "no citation expected", [{"type": "number", "value": 1, "abs_tol": 0}],
             "numeric"),
    ]
    report = evaluate_retrieval(tools.retriever, cases, k=3)
    assert report["cases"] == 2
    assert report["filtered"] == {"recall@3": 0.5, "mrr": 0.5}


def test_agent_eval_is_a_regressgate_report(tools: ToolExecutor) -> None:
    api = ScriptedMessagesAPI([
        fake_response([tool_use_block("t1", "search_filings", {
            "query": "gross margin", "tickers": ["NVDA"], "fiscal_years": [2025],
            "section_items": ["7"]})], "tool_use"),
        fake_response([text_block(
            "Gross margin rose to 75.0% on Data Center mix [NVDA-2025-7-0001].")], "end_turn"),
        fake_response([text_block("Tesla is not in the database.")], "end_turn"),
    ])
    cases = [
        case("m", "Why did margin rise?", [
            {"type": "number", "value": 75.0, "unit": "%", "abs_tol": 0.3},
            {"type": "citation", "any_of": [{"ticker": "NVDA", "section_item": "7"}]},
            {"type": "contains", "terms": ["Data Center"]},
        ], "mixed"),
        case("u", "Tesla revenue?", [{"type": "abstain"}], "unanswerable"),
    ]
    report = evaluate_agent(ResearchAgent(api.create, tools), cases, metadata={"model": "fake"})
    assert report.pass_rate == 1.0
    assert report.check_pass_rates() == {
        "abstain": 1.0, "citation": 1.0, "contains": 1.0, "number": 1.0}
    assert report.cases[0].trials[0].metadata == {"tools": ["search_filings"]}
    assert report.metadata == {"model": "fake"}


def test_an_agent_error_is_a_failed_trial_with_the_reason(tools: ToolExecutor) -> None:
    api = ScriptedMessagesAPI([fake_response([text_block("partial")], "max_tokens")])
    report = evaluate_agent(ResearchAgent(api.create, tools),
                            [case("x", "q", [{"type": "abstain"}], "unanswerable")])
    trial = report.cases[0].trials[0]
    assert not trial.passed and trial.error is not None and "RuntimeError" in trial.error


def test_comparison_table_uses_latest_report_per_model(tmp_path: Path, tools: ToolExecutor) -> None:
    def write(name: str, model: str, answer: str) -> None:
        api = ScriptedMessagesAPI([fake_response([text_block(answer)], "end_turn")])
        report = evaluate_agent(ResearchAgent(api.create, tools),
                                [case("u", "Tesla?", [{"type": "abstain"}], "unanswerable")],
                                metadata={"provider": "openrouter", "model": model})
        report.save(tmp_path / name)

    write("eval-20261020-100000.json", "a/one", "Revenue was $97B.")
    write("eval-20261020-110000.json", "a/one", "Tesla is not in the data.")  # newer wins
    write("eval-20261020-120000.json", "b/two", "Revenue was $97B.")
    (tmp_path / "eval-20261020-130000.json").write_text('{"model": "old format"}')

    table = comparison_table(load_latest_agent_reports(tmp_path)).splitlines()
    assert len(table) == 4
    assert table[2].startswith("| a/one | 1 | 100% | n/a | n/a | 100% |")
    assert table[3].startswith("| b/two | 1 | 0% |")


def test_golden_cases_keep_their_grading_rules() -> None:
    assert len(GOLDEN) == 20
    assert {t for c in GOLDEN.values() for t in c.tags} == {
        "numeric", "narrative", "mixed", "unanswerable"}


@pytest.mark.parametrize("answer", [
    # Real abstentions from Claude Opus 5.5 / Sonnet 5.5 that an earlier phrase list missed.
    "I can't tell you what Tesla's revenue was. My data covers only NVIDIA, Microsoft and "
    "Apple, so any number I gave would be a guess.",
    "I can't give you Tesla's fiscal 2025 revenue because the database has no Tesla data.",
    "Tesla isn't in the database.",
    "There is no Tesla 10-K in my sources.",
])
def test_abstention_phrasings_are_recognised(answer: str) -> None:
    assert passes("abs-tesla", answer) == {"forbidden": True, "abstain": True}


def test_refusal_that_still_invents_a_figure_fails() -> None:
    lying = passes("abs-tesla", "Tesla is not in my database, but its revenue was about $97.7B.")
    assert lying == {"forbidden": False, "abstain": True}
    forecast = passes("abs-forecast",
                      "I cannot predict that. Analysts expect FY2028 revenue near $300B.")
    assert forecast["forbidden"] is False


def test_refusal_must_lead_the_answer() -> None:
    buried = "NVIDIA will grow fast. Revenue should triple. Of course I cannot predict it."
    assert passes("abs-forecast", buried)["abstain"] is False


def test_lead_terms_must_be_in_the_first_sentence() -> None:
    wrong = passes("num-highest-margin",
                   "Microsoft had the highest margin at 69.8%. NVIDIA was 75.0%.")
    right = passes("num-highest-margin",
                   "NVIDIA had the highest margin at 75.0%. Microsoft was 69.8%.")
    assert wrong["lead"] is False
    assert all(right.values())


def test_numbers_respect_units_and_tolerance() -> None:
    assert passes("num-nvda-revenue", "Revenue was $130.5 billion in FY2025.")["number"]
    assert not passes("num-nvda-gross-margin", "Gross margin was 72.7%.")["number"]
    assert not passes("num-nvda-gross-margin", "Gross margin was 75.")["number"]  # not a %


def test_cost_estimate_prices_cache_tokens_and_skips_unknown_models() -> None:
    result = AgentResult("ok", [], [], input_tokens=1_000_000, output_tokens=100_000,
                         cache_read_tokens=600_000, cache_write_tokens=100_000)
    # uncached 300k x $2 + writes 100k x $2.5 + reads 600k x $0.2 + output 100k x $10
    assert estimate_cost("claude-sonnet-5-5", result) == pytest.approx(0.6 + 0.25 + 0.12 + 1.0)
    assert estimate_cost("some/unknown-model", result) is None
    assert estimate_cost("x", AgentResult("ok", [], [], cost_usd=0.5)) == 0.5
