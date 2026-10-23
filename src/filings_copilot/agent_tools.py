"""The three tools the agent can call, as Claude tool definitions plus local executors."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Any

import duckdb

from filings_copilot.filings_store import FilingsStore
from filings_copilot.hybrid_retriever import HybridRetriever, RetrievedChunk, SearchFilters
from filings_copilot.sql_guard import MAX_ROWS, UnsafeSQLError, validate_select
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


# Every tool result is resent to the model on each later turn, so it is size-bounded.
MAX_TOOL_OUTPUT_CHARS = 20_000
QUERY_TIMEOUT_S = 5.0


def _as_list(value: Any) -> list[Any]:
    """Models without schema enforcement may send a scalar where a list is expected."""
    if value is None or value == "":
        return []
    return list(value) if isinstance(value, list | tuple) else [value]


def _truncate(text: str) -> str:
    if len(text) <= MAX_TOOL_OUTPUT_CHARS:
        return text
    omitted = len(text) - MAX_TOOL_OUTPUT_CHARS
    note = f"[truncated: {omitted} more characters; narrow the query]"
    return f"{text[:MAX_TOOL_OUTPUT_CHARS]}\n{note}"


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
            outcome = handler(tool_input)
        # duckdb.Error covers binder/catalog errors from model-written SQL (e.g. a wrong
        # column name) and interrupted queries; the model gets the message and can retry.
        except (UnsafeSQLError, duckdb.Error, ValueError, KeyError, TypeError) as e:
            outcome = ToolOutcome(f"Error: {e}", is_error=True)
        outcome.content = _truncate(outcome.content)
        return outcome

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
            tickers=[str(t) for t in _as_list(tool_input.get("tickers"))],
            fiscal_years=[int(y) for y in _as_list(tool_input.get("fiscal_years"))],
            section_items=[str(s) for s in _as_list(tool_input.get("section_items"))],
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
        cursor = self.store.cursor()
        # A runaway query (huge join, heavy expression) is interrupted instead of holding
        # the single serving CPU; DuckDB raises InterruptException, reported to the model.
        watchdog = threading.Timer(QUERY_TIMEOUT_S, cursor.interrupt)
        watchdog.start()
        try:
            cursor.execute(safe_sql)
            columns = [d[0] for d in cursor.description]
            fetched = cursor.fetchmany(MAX_ROWS)
        finally:
            watchdog.cancel()
        rows = [dict(zip(columns, row, strict=True)) for row in fetched]
        if not rows:
            return ToolOutcome(f"Query returned no rows.\nSQL: {safe_sql}")
        return ToolOutcome(json.dumps({"sql": safe_sql, "rows": rows}, default=str))
