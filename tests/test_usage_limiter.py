from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import ScriptedMessagesAPI, fake_response, text_block
from filings_copilot.agent_tools import ToolExecutor
from filings_copilot.app_factory import Runtime
from filings_copilot.config import Settings
from filings_copilot.http_api import create_app
from filings_copilot.research_agent import ResearchAgent
from filings_copilot.usage_limiter import UsageLimiter, UsageLimitExceeded


class FakeClock:
    def __init__(self, start: float = 1_760_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def test_per_client_window_slides() -> None:
    clock = FakeClock()
    limiter = UsageLimiter(per_client_per_hour=2, clock=clock)
    limiter.check_and_record("a")
    limiter.check_and_record("a")
    limiter.check_and_record("b")  # other clients are independent
    with pytest.raises(UsageLimitExceeded):
        limiter.check_and_record("a")
    clock.now += 3601
    limiter.check_and_record("a")


def test_daily_cap_resets_at_utc_midnight() -> None:
    clock = FakeClock()
    limiter = UsageLimiter(daily_cap=2, clock=clock)
    limiter.check_and_record("a")
    limiter.check_and_record("b")
    with pytest.raises(UsageLimitExceeded, match="daily"):
        limiter.check_and_record("c")
    clock.now += 86400
    limiter.check_and_record("c")


def test_zero_disables_limits() -> None:
    limiter = UsageLimiter()
    for _ in range(100):
        limiter.check_and_record("a")


def test_api_returns_429_when_limited(tools: ToolExecutor) -> None:
    api = ScriptedMessagesAPI([fake_response([text_block("ok")], "end_turn")])
    runtime = Runtime(store=tools.store, agent=ResearchAgent(api.create, tools))
    client = TestClient(create_app(runtime, Settings(demo_questions_per_hour=1)))
    headers = {"x-forwarded-for": "203.0.113.7, 10.0.0.1"}
    assert client.post("/ask", json={"question": "first?"}, headers=headers).status_code == 200
    second = client.post("/ask", json={"question": "again?"}, headers=headers)
    assert second.status_code == 429
    assert "per hour" in second.json()["detail"]


def test_spoofed_forwarded_entries_do_not_reset_the_client_limit(tools: ToolExecutor) -> None:
    api = ScriptedMessagesAPI([fake_response([text_block("ok")], "end_turn")])
    runtime = Runtime(store=tools.store, agent=ResearchAgent(api.create, tools))
    client = TestClient(create_app(runtime, Settings(demo_questions_per_hour=1)))
    # The platform appends the real address last; the client controls what comes before.
    first = {"x-forwarded-for": "1.1.1.1, 203.0.113.7"}
    spoofed = {"x-forwarded-for": "9.9.9.9, 203.0.113.7"}
    assert client.post("/ask", json={"question": "first?"}, headers=first).status_code == 200
    assert client.post("/ask", json={"question": "again?"}, headers=spoofed).status_code == 429


def test_agent_failures_reach_the_client_without_internal_details(tools: ToolExecutor) -> None:
    def explode(**_: object) -> None:
        raise RuntimeError("provider body with secret-looking details")

    runtime = Runtime(store=tools.store, agent=ResearchAgent(explode, tools))
    client = TestClient(create_app(runtime, Settings()))
    with client.stream("POST", "/ask/stream", json={"question": "boom?"}) as r:
        body = "".join(r.iter_text())
    assert "event: error" in body and "internal error (ref " in body
    assert "secret-looking" not in body
