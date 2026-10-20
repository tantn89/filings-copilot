from __future__ import annotations

from typing import Any

from filings_copilot.financial_facts_loader import annual_metrics


def fact(val: float, start: str | None, end: str, filed: str, form: str = "10-K") -> dict[str, Any]:
    f: dict[str, Any] = {"val": val, "end": end, "filed": filed, "form": form, "accn": f"a-{filed}"}
    if start:
        f["start"] = start
    return f


def company_facts(gaap: dict[str, list[dict[str, Any]]], unit: str = "USD") -> dict[str, Any]:
    return {"cik": 1, "facts": {"us-gaap": {k: {"units": {unit: v}} for k, v in gaap.items()}}}


def by_key(rows: list[Any]) -> dict[tuple[str, int], float]:
    return {(r.metric, r.fiscal_year): r.value for r in rows}


def test_keeps_annual_10k_durations_only() -> None:
    rows = annual_metrics(company_facts({"NetIncomeLoss": [
        fact(100, "2023-01-01", "2023-12-31", "2024-02-01"),
        fact(30, "2023-07-01", "2023-09-30", "2023-11-01"),               # quarter
        fact(999, "2023-01-01", "2023-12-31", "2024-02-01", form="10-Q"),  # wrong form
    ]}), "ACME")
    assert by_key(rows) == {("net_income", 2023): 100}


def test_restatement_in_later_filing_wins() -> None:
    rows = annual_metrics(company_facts({"NetIncomeLoss": [
        fact(100, "2023-01-01", "2023-12-31", "2024-02-01"),
        fact(95, "2023-01-01", "2023-12-31", "2025-02-01"),
    ]}), "ACME")
    assert by_key(rows) == {("net_income", 2023): 95}


def test_preferred_concept_beats_fallback_but_fallback_fills_gaps() -> None:
    rows = annual_metrics(company_facts({
        "RevenueFromContractWithCustomerExcludingAssessedTax": [
            fact(500, "2023-01-01", "2023-12-31", "2024-02-01")],
        "Revenues": [
            fact(510, "2023-01-01", "2023-12-31", "2024-02-01"),
            fact(400, "2018-01-01", "2018-12-31", "2019-02-01")],
    }), "ACME")
    assert by_key(rows) == {("revenue", 2023): 500, ("revenue", 2018): 400}


def test_balance_sheet_items_are_instants() -> None:
    rows = annual_metrics(company_facts({"Assets": [
        fact(1000, None, "2023-12-31", "2024-02-01"),
        fact(7, "2023-01-01", "2023-12-31", "2024-02-01"),
    ]}), "ACME")
    assert by_key(rows) == {("total_assets", 2023): 1000}
