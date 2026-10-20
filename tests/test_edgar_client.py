from __future__ import annotations

import httpx
import pytest

from filings_copilot.edgar_client import EdgarClient

SUBMISSIONS = {
    "filings": {"recent": {
        "form": ["10-K", "10-Q", "10-K/A", "10-K", "8-K"],
        "accessionNumber": ["0001-25-1", "0001-24-9", "0001-24-5", "0001-24-1", "0001-23-7"],
        "filingDate": ["2025-02-26", "2024-11-20", "2024-06-01", "2024-02-21", "2023-05-01"],
        "reportDate": ["2025-01-26", "2024-10-27", "2024-01-28", "2024-01-28", ""],
        "primaryDocument": ["a.htm", "b.htm", "c.htm", "d.htm", "e.htm"],
    }}
}


def handler(request: httpx.Request) -> httpx.Response:
    assert "@" in request.headers["User-Agent"]
    if request.url.path.endswith("company_tickers.json"):
        return httpx.Response(200, json={"0": {"cik_str": 1045810, "ticker": "NVDA",
                                               "title": "NVIDIA CORP"}})
    if "submissions" in request.url.path:
        return httpx.Response(200, json=SUBMISSIONS)
    return httpx.Response(404)


@pytest.fixture
def client() -> EdgarClient:
    return EdgarClient("tests t@example.com", 1000, transport=httpx.MockTransport(handler))


def test_requires_contact_in_user_agent() -> None:
    with pytest.raises(ValueError, match="User-Agent"):
        EdgarClient("anonymous")


def test_annual_reports_keeps_original_10ks_in_requested_years(client: EdgarClient) -> None:
    company = client.company("nvda")
    refs = client.annual_reports(company, {2024, 2025})
    assert [(r.accession_number, r.fiscal_year) for r in refs] == [
        ("0001-25-1", 2025), ("0001-24-1", 2024)]
    assert refs[0].url == "https://www.sec.gov/Archives/edgar/data/1045810/0001251/a.htm"


def test_unknown_ticker(client: EdgarClient) -> None:
    with pytest.raises(ValueError, match="Unknown ticker"):
        client.company("ZZZZ")


def test_retries_transient_connection_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("filings_copilot.edgar_client.time.sleep", lambda _: None)
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectTimeout("timed out", request=request)
        return handler(request)

    client = EdgarClient("tests t@example.com", 1000, transport=httpx.MockTransport(flaky))
    assert client.company("NVDA").cik == 1045810
    assert calls["n"] == 3
