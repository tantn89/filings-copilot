"""Thin client for SEC EDGAR public APIs.

Endpoints used:
- https://www.sec.gov/files/company_tickers.json            ticker -> CIK map
- https://data.sec.gov/submissions/CIK##########.json       filing index per company
- https://data.sec.gov/api/xbrl/companyfacts/CIK#####.json  all XBRL facts per company
- https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc} filing documents

SEC fair-access rules: <= 10 requests/second and a User-Agent that identifies you.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/{document}"


@dataclass(frozen=True)
class Company:
    cik: int
    ticker: str
    name: str


@dataclass(frozen=True)
class FilingRef:
    cik: int
    ticker: str
    form: str
    accession_number: str
    filing_date: str
    report_date: str
    primary_document: str

    @property
    def url(self) -> str:
        return ARCHIVE_URL.format(
            cik=self.cik,
            accession_nodash=self.accession_number.replace("-", ""),
            document=self.primary_document,
        )

    @property
    def fiscal_year(self) -> int:
        # Fiscal year is named after the calendar year in which the period ends,
        # which matches how most filers label it (NVDA FY2025 ends Jan 2025).
        return int(self.report_date[:4])


class _RateLimiter:
    """Simple thread-safe minimum-interval limiter."""

    def __init__(self, per_second: float) -> None:
        self._interval = 1.0 / per_second
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._next_at - now
            self._next_at = max(now, self._next_at) + self._interval
        if delay > 0:
            time.sleep(delay)


class EdgarClient:
    def __init__(
        self,
        user_agent: str,
        max_requests_per_second: float = 8.0,
        transport: httpx.BaseTransport | None = None,
        force_ipv4: bool = False,
    ) -> None:
        if "@" not in user_agent:
            raise ValueError(
                "SEC requires a User-Agent with a contact email. "
                "Set FILINGS_SEC_USER_AGENT='your-app you@example.com'."
            )
        self._limiter = _RateLimiter(max_requests_per_second)
        if transport is None and force_ipv4:
            # Binding to 0.0.0.0 makes the socket IPv4-only; useful on networks where
            # IPv6 resolves but does not route (www.sec.gov then times out).
            transport = httpx.HTTPTransport(local_address="0.0.0.0")
        self._http = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=30.0,
            follow_redirects=True,
            transport=transport,
        )
        self._tickers: dict[str, Company] | None = None

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> EdgarClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _get(self, url: str, attempts: int = 5) -> httpx.Response:
        for attempt in range(attempts):
            last_try = attempt == attempts - 1
            self._limiter.wait()
            try:
                response = self._http.get(url)
            except httpx.TransportError:
                # Connection resets and timeouts happen on long ingests; retry them.
                if last_try:
                    raise
                time.sleep(2**attempt)
                continue
            # 429/503 mean we are being throttled; back off and retry.
            if response.status_code in (429, 503) and not last_try:
                time.sleep(2**attempt)
                continue
            response.raise_for_status()
            return response
        raise AssertionError("unreachable")

    def _get_json(self, url: str) -> Any:
        return self._get(url).json()

    def company(self, ticker: str) -> Company:
        if self._tickers is None:
            raw = self._get_json(TICKERS_URL)
            self._tickers = {
                row["ticker"].upper(): Company(
                    int(row["cik_str"]), row["ticker"].upper(), row["title"]
                )
                for row in raw.values()
            }
        try:
            return self._tickers[ticker.upper()]
        except KeyError:
            raise ValueError(f"Unknown ticker: {ticker}") from None

    def annual_reports(self, company: Company, fiscal_years: set[int]) -> list[FilingRef]:
        """Original 10-K filings (amendments excluded) whose period ends in `fiscal_years`."""
        recent = self._get_json(SUBMISSIONS_URL.format(cik=company.cik))["filings"]["recent"]
        refs = []
        for i, form in enumerate(recent["form"]):
            if form != "10-K" or not recent["reportDate"][i]:
                continue
            ref = FilingRef(
                cik=company.cik,
                ticker=company.ticker,
                form=form,
                accession_number=recent["accessionNumber"][i],
                filing_date=recent["filingDate"][i],
                report_date=recent["reportDate"][i],
                primary_document=recent["primaryDocument"][i],
            )
            if ref.fiscal_year in fiscal_years:
                refs.append(ref)
        return refs

    def filing_html(self, ref: FilingRef) -> str:
        return self._get(ref.url).text

    def company_facts(self, company: Company) -> dict[str, Any]:
        result: dict[str, Any] = self._get_json(COMPANY_FACTS_URL.format(cik=company.cik))
        return result
