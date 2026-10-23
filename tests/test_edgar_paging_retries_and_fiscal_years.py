from __future__ import annotations

from datetime import date
from typing import Any

import httpx
import pytest

from filings_copilot.edgar_client import Company, EdgarClient, FilingRef
from filings_copilot.financial_facts_loader import annual_metrics
from filings_copilot.fiscal_calendar import fiscal_year

ACME = Company(1, "ACME", "ACME CORP")


def page(*filings: tuple[str, str, str]) -> dict[str, list[str]]:
    """(form, accession, report_date) rows in the submissions-index column layout."""
    return {
        "form": [f[0] for f in filings],
        "accessionNumber": [f[1] for f in filings],
        "filingDate": [f[2][:4] + "-03-01" for f in filings],
        "reportDate": [f[2] for f in filings],
        "primaryDocument": [f"{f[1]}.htm" for f in filings],
    }


def client(handler: Any) -> EdgarClient:
    return EdgarClient("t t@example.com", 1000, transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(("end", "expected"), [
    (date(2025, 1, 26), 2025),   # NVIDIA: year ends late January
    (date(2025, 9, 27), 2025),   # Apple: last Saturday of September
    (date(2022, 1, 1), 2021),    # 53-week year that spills into January
    (date(2022, 12, 31), 2022),
])
def test_fiscal_year_rule(end: date, expected: int) -> None:
    assert fiscal_year(end) == expected


def test_back_to_back_52_53_week_years_no_longer_collide() -> None:
    facts = {"cik": 1, "facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        {"val": 100, "start": "2021-01-03", "end": "2022-01-01", "filed": "2022-02-20",
         "form": "10-K", "accn": "a1"},
        {"val": 200, "start": "2022-01-02", "end": "2022-12-31", "filed": "2023-02-20",
         "form": "10-K", "accn": "a2"},
    ]}}}}}
    rows = {r.fiscal_year: r.value for r in annual_metrics(facts, "ACME")}
    assert rows == {2021: 100, 2022: 200}
    a = FilingRef(1, "ACME", "10-K", "a1", "2022-02-20", "2022-01-01", "a.htm")
    b = FilingRef(1, "ACME", "10-K", "a2", "2023-02-20", "2022-12-31", "b.htm")
    assert (a.fiscal_year, b.fiscal_year) == (2021, 2022)


def test_older_10ks_are_found_in_paged_submission_files() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        if request.url.path.endswith("CIK0000000001.json"):
            return httpx.Response(200, json={"filings": {
                "recent": page(("10-K", "r-2024", "2024-12-31"), ("8-K", "x", "2024-06-01")),
                "files": [{"name": "CIK0000000001-submissions-001.json"},
                          {"name": "CIK0000000001-submissions-002.json"}],
            }})
        if request.url.path.endswith("-001.json"):
            return httpx.Response(200, json=page(("10-K", "o-2016", "2016-12-31")))
        return httpx.Response(500)

    refs = client(handler).annual_reports(ACME, {2024, 2016})
    assert [r.accession_number for r in refs] == ["r-2024", "o-2016"]
    assert not any(p.endswith("-002.json") for p in requested)  # stopped once complete


def test_recent_page_alone_when_it_has_every_year() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("CIK0000000001.json"), "no extra pages needed"
        return httpx.Response(200, json={"filings": {
            "recent": page(("10-K", "r-2024", "2024-12-31")),
            "files": [{"name": "CIK0000000001-submissions-001.json"}]}})

    assert [r.accession_number for r in client(handler).annual_reports(ACME, {2024})] == [
        "r-2024"]


def test_server_errors_are_retried_honouring_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("filings_copilot.edgar_client.time.sleep", sleeps.append)
    statuses = iter([502, 429, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        status = next(statuses)
        headers = {"Retry-After": "7"} if status == 429 else {}
        return httpx.Response(status, headers=headers, json={"0": {
            "cik_str": 1, "ticker": "ACME", "title": "ACME CORP"}})

    assert client(handler).company("acme") == ACME
    backoffs = [s for s in sleeps if s >= 0.5]  # ignore the rate limiter's tiny spacing
    assert backoffs == [1.0, 7.0]


def test_filing_html_falls_back_to_windows_1252() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content="Revenue — “strong”".encode("cp1252"))

    ref = FilingRef(1, "ACME", "10-K", "a1", "2025-02-01", "2024-12-31", "a.htm")
    assert client(handler).filing_html(ref) == "Revenue — “strong”"
