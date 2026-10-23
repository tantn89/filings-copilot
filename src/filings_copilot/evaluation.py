"""Evaluation harness: retrieval quality (offline, free) and end-to-end agent quality.

Golden cases are regressgate cases (`evals/golden_cases.jsonl`) with deterministic checks:
numbers against XBRL ground truth, citations against the expected filing section, refusal
phrases and forbidden patterns. Agent runs are regressgate reports, so two runs can be
gated with `regressgate compare base.json head.json` and noise is told apart from change.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from regressgate import Case, RunReport, TargetResult, run_suite

from filings_copilot.hybrid_retriever import HybridRetriever, SearchFilters
from filings_copilot.research_agent import AgentResult, ResearchAgent

# List prices in USD per million tokens: (input, output, cache read). Used when the
# provider does not report billed cost (it does on OpenRouter). Cache writes bill at
# 1.25x input (5-minute TTL). Unknown models get no estimate rather than a wrong one.
PRICE_PER_MTOK: dict[str, tuple[float, float, float]] = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}
CACHE_WRITE_MULTIPLIER = 1.25

def estimate_cost(model: str, result: AgentResult) -> float | None:
    if result.cost_usd is not None:
        return result.cost_usd
    if model not in PRICE_PER_MTOK:
        return None
    price_in, price_out, price_cache_read = PRICE_PER_MTOK[model]
    uncached = result.input_tokens - result.cache_read_tokens - result.cache_write_tokens
    return (
        uncached * price_in
        + result.cache_write_tokens * price_in * CACHE_WRITE_MULTIPLIER
        + result.cache_read_tokens * price_cache_read
        + result.output_tokens * price_out
    ) / 1e6


# ---------- retrieval -----------------------------------------------------------------


@dataclass
class RetrievalCaseResult:
    id: str
    filtered_rank: int | None
    unfiltered_rank: int | None


def _check_params(case: Case, check_type: str) -> dict[str, Any] | None:
    return next((c.params for c in case.checks if c.type == check_type), None)


def _first_relevant_rank(
    hits: list[Any], expected: list[dict[str, Any]], terms: list[str]
) -> int | None:
    for rank, hit in enumerate(hits, start=1):
        for exp in expected:
            if hit.ticker != exp["ticker"] or hit.section_item != exp["section_item"]:
                continue
            if "fiscal_year" in exp and hit.fiscal_year != exp["fiscal_year"]:
                continue
            if all(t.lower() in hit.text.lower() for t in terms):
                return rank
    return None


def evaluate_retrieval(
    retriever: HybridRetriever, cases: list[Case], k: int = 5
) -> dict[str, Any]:
    """Recall@k and MRR for cases with a `citation` check.

    A hit is a passage from an expected company + section that contains every term of the
    case's `contains` check. 'filtered' mimics the agent (it passes the ticker it is asking
    about); 'unfiltered' is the harder setting where retrieval alone must find the company.
    """
    results = []
    for case in cases:
        citation = _check_params(case, "citation")
        if not citation:
            continue
        expected = citation["any_of"]
        terms = (_check_params(case, "contains") or {}).get("terms", [])
        tickers = sorted({e["ticker"] for e in expected})
        question = str(case.input)
        filtered = retriever.search(question, SearchFilters(tickers=tickers), k=k)
        unfiltered = retriever.search(question, k=k)
        results.append(RetrievalCaseResult(
            case.id,
            _first_relevant_rank(filtered, expected, terms),
            _first_relevant_rank(unfiltered, expected, terms),
        ))

    def summary(attr: str) -> dict[str, float]:
        ranks = [getattr(r, attr) for r in results]
        return {
            f"recall@{k}": round(sum(r is not None for r in ranks) / len(ranks), 3),
            "mrr": round(sum(1 / r for r in ranks if r) / len(ranks), 3),
        }

    return {
        "cases": len(results),
        "filtered": summary("filtered_rank"),
        "unfiltered": summary("unfiltered_rank"),
        "per_case": [asdict(r) for r in results],
    }


# ---------- end-to-end agent ----------------------------------------------------------


def agent_target(agent: ResearchAgent) -> Callable[[Any], TargetResult]:
    """Adapt the research agent to a regressgate target."""

    def answer(question: Any) -> TargetResult:
        result = agent.ask(str(question))
        if not result.answer:
            # Recorded by regressgate as a failed trial with this error, not a wrong answer.
            raise RuntimeError(result.stop_reason or "agent returned no answer")
        return TargetResult(
            output=result.answer,
            citations=[{"ticker": c.ticker, "section_item": c.section_item,
                        "fiscal_year": c.fiscal_year, "chunk_id": c.chunk_id}
                       for c in result.citations],
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=estimate_cost(agent.model, result),
            metadata={"tools": [c["name"] for c in result.tool_calls]},
        )

    return answer


def evaluate_agent(
    agent: ResearchAgent,
    cases: list[Case],
    *,
    repeats: int = 1,
    concurrency: int = 1,
    metadata: dict[str, Any] | None = None,
) -> RunReport:
    return run_suite(cases, agent_target(agent), target_name=f"filings-copilot:{agent.model}",
                     repeats=repeats, concurrency=concurrency, metadata=metadata)


# ---------- model comparison ----------------------------------------------------------


def load_latest_agent_reports(results_dir: Path) -> list[RunReport]:
    """The most recent agent eval report per provider/model, oldest file first.

    Files that are not regressgate reports (retrieval-only runs, older formats) are skipped.
    """
    latest: dict[tuple[str, str], RunReport] = {}
    for path in sorted(results_dir.glob("eval-*.json")):  # timestamped names sort by time
        try:
            report = RunReport.load(path)
        except (ValueError, json.JSONDecodeError):
            continue
        key = (str(report.metadata.get("provider", "")), str(report.metadata.get("model", "")))
        latest[key] = report
    return list(latest.values())


def comparison_table(reports: list[RunReport]) -> str:
    def pct(value: float | None) -> str:
        return "n/a" if value is None else f"{100 * value:.0f}%"

    rows = [
        "| Model | Trials per case | Pass rate | Numeric | Citation | Abstain | p50 latency "
        "| Cost / question |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(reports, key=lambda r: -r.pass_rate):
        checks = r.check_pass_rates()
        cost = r.mean_cost_usd()
        p50 = statistics.median(t.latency_s for t in r.trials)
        rows.append(
            f"| {r.metadata.get('model', r.target)} | {r.repeats} | {pct(r.pass_rate)} | "
            f"{pct(checks.get('number'))} | {pct(checks.get('citation'))} | "
            f"{pct(checks.get('abstain'))} | {p50:.1f}s | "
            f"{'n/a' if cost is None else f'${cost:.4f}'} |"
        )
    return "\n".join(rows)
