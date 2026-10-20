"""The three tools the agent can call, as Claude tool definitions plus local executors."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from filings_copilot.filings_store import FilingsStore
from filings_copilot.hybrid_retriever import HybridRetriever, RetrievedChunk, SearchFilters
from filings_copilot.sql_guard import UnsafeSQLError, validate_select
from filings_copilot.tenk_section_parser import SECTION_TITLES

FINANCIALS_SCHEMA = """\
annual_financials(ticker, metric, fiscal_year, period_start, period_end, value, unit,
                  xbrl_concept, accession_number, filed)
  one row per (ticker, metric, fiscal_year); value is in `unit` (USD, or USD/shares for EPS)
  metric in: revenue, cost_of_revenue, gross_profit, research_and_development,
             operating_income, net_income, eps_diluted, operating_cash_flow,
             capital_expenditure, total_assets, total_liabilities, stockholders_equity,
             cash_and_equivalents
companies(ticker, cik, name)
filings(accession_number, ticker, form, fiscal_year, filing_date, report_date, url)"""

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "list_companies",
        "description": "List the companies and fiscal years available in the database. "
        "Call this first when unsure what data exists.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "strict": True,
    },
    {
        "name": "search_filings",
        "description": "Hybrid keyword + semantic search over 10-K narrative text "
        "(sections: " + ", ".join(f"{k} {v}" for k, v in SECTION_TITLES.items()) + "). "
        "Use for explanations, risks, strategy and management commentary. Returns passages "
        "with a citation id like [NVDA-2025-7-0012]; cite those ids in the answer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look for, in plain English."},
                "tickers": {"type": "array", "items": {"type": "string"}},
                "fiscal_years": {"type": "array", "items": {"type": "integer"}},
                "section_items": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(SECTION_TITLES)},
                },
            },
            "required": ["query", "tickers", "fiscal_years", "section_items"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "query_financials",
        "description": "Run one read-only DuckDB SELECT over audited XBRL financials. "
        "Use for every number you state. Schema:\n" + FINANCIALS_SCHEMA,
        "input_schema": {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


@dataclass
class ToolOutcome:
    content: str
    is_error: bool = False
    citations: list[RetrievedChunk] = field(default_factory=list)


class ToolExecutor:
    def __init__(self, store: FilingsStore, retriever: HybridRetriever) -> None:
        self.store = store
        self.retriever = retriever

    def run(self, name: str, tool_input: dict[str, Any]) -> ToolOutcome:
        handler = {
            "list_companies": self._list_companies,
            "search_filings": self._search_filings,
            "query_financials": self._query_financials,
        }.get(name)
        if handler is None:
            return ToolOutcome(f"Unknown tool: {name}", is_error=True)
        try:
            return handler(tool_input)
        except (UnsafeSQLError, ValueError, KeyError, TypeError) as e:
            return ToolOutcome(f"Error: {e}", is_error=True)

    def _list_companies(self, _: dict[str, Any]) -> ToolOutcome:
        rows = self.store.cursor().execute(
            """
            SELECT c.ticker, c.name, list(DISTINCT f.fiscal_year ORDER BY f.fiscal_year)
            FROM companies c LEFT JOIN filings f USING (ticker)
            GROUP BY ALL ORDER BY c.ticker
            """
        ).fetchall()
        return ToolOutcome(json.dumps(
            [{"ticker": t, "name": n, "fiscal_years_with_10k_text": y} for t, n, y in rows]
        ))

    def _search_filings(self, tool_input: dict[str, Any]) -> ToolOutcome:
        filters = SearchFilters(
            tickers=tool_input.get("tickers") or (),
            fiscal_years=tool_input.get("fiscal_years") or (),
            section_items=tool_input.get("section_items") or (),
        )
        hits = self.retriever.search(str(tool_input["query"]), filters)
        if not hits:
            return ToolOutcome("No passages matched. Try broader terms or fewer filters.")
        passages = [
            f"[{h.chunk_id}] {h.ticker} FY{h.fiscal_year} Item {h.section_item} "
            f"({h.section_title}):\n{h.text}"
            for h in hits
        ]
        return ToolOutcome("\n\n".join(passages), citations=hits)

    def _query_financials(self, tool_input: dict[str, Any]) -> ToolOutcome:
        safe_sql = validate_select(str(tool_input["sql"]))
        cursor = self.store.cursor().execute(safe_sql)
        columns = [d[0] for d in cursor.description]
        rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        if not rows:
            return ToolOutcome(f"Query returned no rows.\nSQL: {safe_sql}")
        return ToolOutcome(json.dumps({"sql": safe_sql, "rows": rows}, default=str))
