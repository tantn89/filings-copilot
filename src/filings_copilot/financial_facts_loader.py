"""Turn the SEC companyfacts JSON into tidy annual metric rows.

Raw XBRL has hundreds of concepts per company, the same value repeated in every later
filing that restates it, and several concept names for the same idea (revenue alone has
at least three). The agent should not have to know any of that, so we expose one row per
(company, metric, fiscal_year) with a friendly metric name.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

# metric name -> (unit, kind, candidate us-gaap concepts in priority order)
# kind "flow" = measured over the fiscal year; "stock" = balance at fiscal year end.
METRICS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "revenue": ("USD", "flow", (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    )),
    "cost_of_revenue": ("USD", "flow", ("CostOfRevenue", "CostOfGoodsAndServicesSold")),
    "gross_profit": ("USD", "flow", ("GrossProfit",)),
    "research_and_development": ("USD", "flow", ("ResearchAndDevelopmentExpense",)),
    "operating_income": ("USD", "flow", ("OperatingIncomeLoss",)),
    "net_income": ("USD", "flow", ("NetIncomeLoss",)),
    "eps_diluted": ("USD/shares", "flow", ("EarningsPerShareDiluted",)),
    "operating_cash_flow": ("USD", "flow", ("NetCashProvidedByUsedInOperatingActivities",)),
    "capital_expenditure": ("USD", "flow", (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",  # used by e.g. NVIDIA (PP&E + intangibles)
    )),
    "total_assets": ("USD", "stock", ("Assets",)),
    "total_liabilities": ("USD", "stock", ("Liabilities",)),
    "stockholders_equity": ("USD", "stock", ("StockholdersEquity",)),
    "cash_and_equivalents": ("USD", "stock", ("CashAndCashEquivalentsAtCarryingValue",)),
}


@dataclass(frozen=True)
class MetricRow:
    cik: int
    ticker: str
    metric: str
    fiscal_year: int
    period_start: date | None
    period_end: date
    value: float
    unit: str
    concept: str
    accession_number: str
    filed: date


def _is_annual_duration(start: date, end: date) -> bool:
    return 350 <= (end - start).days <= 380


def annual_metrics(company_facts: dict[str, Any], ticker: str) -> list[MetricRow]:
    cik = int(company_facts["cik"])
    gaap = company_facts.get("facts", {}).get("us-gaap", {})
    rows: list[MetricRow] = []

    for metric, (unit, kind, concepts) in METRICS.items():
        chosen: dict[int, MetricRow] = {}
        for concept in concepts:
            for fact in gaap.get(concept, {}).get("units", {}).get(unit, []):
                if fact.get("form") != "10-K":
                    continue
                end = date.fromisoformat(fact["end"])
                start = date.fromisoformat(fact["start"]) if "start" in fact else None
                if kind == "flow" and (start is None or not _is_annual_duration(start, end)):
                    continue
                if kind == "stock" and start is not None:
                    continue
                row = MetricRow(
                    cik=cik,
                    ticker=ticker,
                    metric=metric,
                    fiscal_year=end.year,
                    period_start=start,
                    period_end=end,
                    value=float(fact["val"]),
                    unit=unit,
                    concept=concept,
                    accession_number=fact["accn"],
                    filed=date.fromisoformat(fact["filed"]),
                )
                existing = chosen.get(row.fiscal_year)
                # Higher-priority concept wins; within a concept the latest filing wins,
                # so restatements replace originally reported numbers.
                if existing is None or (
                    existing.concept == concept and row.filed > existing.filed
                ):
                    chosen[row.fiscal_year] = row
        rows.extend(chosen.values())
    return rows
