"""Evaluation harness: retrieval quality (offline, free) and end-to-end agent quality.

Grading is deterministic on purpose - numbers are checked against XBRL ground truth,
citations against the expected filing section, abstention against a phrase list - so a
score change always means the system changed, not the grader.
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from filings_copilot.hybrid_retriever import HybridRetriever, SearchFilters
from filings_copilot.research_agent import AgentResult, ResearchAgent

# USD per million tokens for claude-opus-5-5; cache reads are cheaper, so this is an
# upper bound when caching is active.
PRICE_PER_MTOK = {"input": 4.00, "output": 20.00}

_SCALE = {"trillion": 1e12, "t": 1e12, "billion": 1e9, "bn": 1e9, "b": 1e9,
          "million": 1e6, "mn": 1e6, "m": 1e6, "thousand": 1e3, "k": 1e3}
_NUMBER = re.compile(
    r"(?P<dollar>\$)?\s?(?P<num>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<dec>\d+))?\s?"
    r"(?P<suffix>%|(?:percent|trillion|billion|million|thousand|bn|mn|[tbmk])\b)?",
    re.IGNORECASE,
)
_ABSTAIN = re.compile(
    r"(not (?:available|in|included|contain|have)|no (?:data|information|10-k)|"
    r"don't have|do not have|cannot (?:predict|forecast|provide)|can't (?:predict|forecast)|"
    r"unable to|isn't (?:available|in)|outside (?:the|my) data)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ExtractedNumber:
    value: float
    unit: str  # "%" | "$" | ""


def extract_numbers(text: str) -> list[ExtractedNumber]:
    found = []
    for m in _NUMBER.finditer(text.replace(chr(0x2212), "-")):
        value = float(m.group("num").replace(",", "") + "." + (m.group("dec") or "0"))
        suffix = (m.group("suffix") or "").lower()
        if suffix in ("%", "percent"):
            found.append(ExtractedNumber(value, "%"))
        elif suffix in _SCALE:
            found.append(ExtractedNumber(value * _SCALE[suffix], "$" if m.group("dollar") else ""))
        else:
            found.append(ExtractedNumber(value, "$" if m.group("dollar") else ""))
    return found


def number_matches(expected: dict[str, Any], found: list[ExtractedNumber]) -> bool:
    unit = expected.get("unit", "")
    target = float(expected["value"])
    for n in found:
        if unit == "%" and n.unit != "%":
            continue
        if unit != "%" and n.unit == "%":
            continue
        if "abs_tol" in expected and abs(n.value - target) <= expected["abs_tol"]:
            return True
        if "rel_tol" in expected and abs(n.value - target) <= expected["rel_tol"] * abs(target):
            return True
    return False


def load_cases(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# ---------- retrieval -----------------------------------------------------------------


@dataclass
class RetrievalCaseResult:
    id: str
    filtered_rank: int | None
    unfiltered_rank: int | None


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
    retriever: HybridRetriever, cases: list[dict[str, Any]], k: int = 5
) -> dict[str, Any]:
    """Recall@k and MRR for questions with an expected citation.

    'filtered' mimics the agent (it passes the ticker it is asking about); 'unfiltered'
    is the harder setting where retrieval alone must find the right company.
    """
    results = []
    for case in cases:
        expected = case.get("expected_citations")
        if not expected:
            continue
        terms = case.get("expected_terms", [])
        tickers = sorted({e["ticker"] for e in expected})
        filtered = retriever.search(case["question"], SearchFilters(tickers=tickers), k=k)
        unfiltered = retriever.search(case["question"], k=k)
        results.append(RetrievalCaseResult(
            case["id"],
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


@dataclass
class AgentCaseResult:
    id: str
    kind: str
    passed: bool
    checks: dict[str, bool]
    latency_s: float
    input_tokens: int
    output_tokens: int
    answer: str
    tools_used: list[str] = field(default_factory=list)


def grade(case: dict[str, Any], result: AgentResult) -> dict[str, bool]:
    checks: dict[str, bool] = {}
    answer = result.answer
    if case["kind"] == "unanswerable":
        checks["abstained"] = bool(answer) and bool(_ABSTAIN.search(answer))
        return checks
    checks["answered"] = bool(answer)
    if case.get("expected_numbers"):
        found = extract_numbers(answer)
        checks["numbers"] = all(number_matches(e, found) for e in case["expected_numbers"])
    if case.get("expected_citations"):
        checks["citation"] = any(
            c.ticker == e["ticker"] and c.section_item == e["section_item"]
            and ("fiscal_year" not in e or c.fiscal_year == e["fiscal_year"])
            for c in result.citations for e in case["expected_citations"]
        )
    if case.get("expected_terms"):
        checks["terms"] = all(t.lower() in answer.lower() for t in case["expected_terms"])
    return checks


def evaluate_agent(agent: ResearchAgent, cases: list[dict[str, Any]]) -> dict[str, Any]:
    results = []
    for case in cases:
        r = agent.ask(case["question"])
        checks = grade(case, r)
        results.append(AgentCaseResult(
            id=case["id"], kind=case["kind"], passed=all(checks.values()), checks=checks,
            latency_s=r.latency_s, input_tokens=r.input_tokens, output_tokens=r.output_tokens,
            answer=r.answer, tools_used=[c["name"] for c in r.tool_calls],
        ))

    def rate(predicate: Any) -> float | None:
        subset = [r for r in results if predicate(r)]
        return round(sum(r.passed for r in subset) / len(subset), 3) if subset else None

    def check_rate(name: str) -> float | None:
        values = [r.checks[name] for r in results if name in r.checks]
        return round(sum(values) / len(values), 3) if values else None

    latencies = sorted(r.latency_s for r in results)
    tokens_in = sum(r.input_tokens for r in results)
    tokens_out = sum(r.output_tokens for r in results)
    cost = (tokens_in * PRICE_PER_MTOK["input"] + tokens_out * PRICE_PER_MTOK["output"]) / 1e6
    return {
        "cases": len(results),
        "pass_rate": rate(lambda _: True),
        "pass_rate_by_kind": {k: rate(lambda r, k=k: r.kind == k)
                              for k in sorted({r.kind for r in results})},
        "numeric_accuracy": check_rate("numbers"),
        "citation_accuracy": check_rate("citation"),
        "abstention_accuracy": check_rate("abstained"),
        "latency_p50_s": statistics.median(latencies) if latencies else None,
        "latency_p95_s": latencies[int(0.95 * (len(latencies) - 1))] if latencies else None,
        "avg_cost_usd_upper_bound": round(cost / max(len(results), 1), 4),
        "per_case": [asdict(r) for r in results],
    }
