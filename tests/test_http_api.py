from __future__ import annotations

from fastapi.testclient import TestClient

from conftest import ScriptedMessagesAPI, fake_response, text_block, tool_use_block
from filings_copilot.agent_tools import ToolExecutor
from filings_copilot.app_factory import Runtime
from filings_copilot.http_api import create_app
from filings_copilot.research_agent import ResearchAgent


def make_client(tools: ToolExecutor, responses: list) -> TestClient:  # type: ignore[type-arg]
    agent = ResearchAgent(ScriptedMessagesAPI(responses).create, tools)
    return TestClient(create_app(Runtime(store=tools.store, agent=agent)))


def test_health_and_companies(tools: ToolExecutor) -> None:
    client = make_client(tools, [])
    assert client.get("/health").json() == {"status": "ok", "filings": 1, "chunks": 5}
    assert client.get("/companies").json()[0]["ticker"] == "NVDA"


def test_ask_returns_answer_with_citations(tools: ToolExecutor) -> None:
    client = make_client(tools, [
        fake_response([tool_use_block("t1", "search_filings", {
            "query": "export", "tickers": [], "fiscal_years": [], "section_items": []})],
            "tool_use"),
        fake_response([text_block("Export controls are a risk [NVDA-2025-1A-0002].")], "end_turn"),
    ])
    body = client.post("/ask", json={"question": "What about export controls?"}).json()
    assert body["answer"].startswith("Export controls")
    assert body["citations"][0]["chunk_id"] == "NVDA-2025-1A-0002"
    assert "events" not in body


def test_ask_stream_emits_tool_and_answer_events(tools: ToolExecutor) -> None:
    client = make_client(tools, [
        fake_response([tool_use_block("t1", "list_companies", {})], "tool_use"),
        fake_response([text_block("NVDA only.")], "end_turn"),
    ])
    with client.stream("POST", "/ask/stream", json={"question": "Which companies?"}) as r:
        text = "".join(r.iter_text())
    events = [line.split(":", 1)[1].strip() for line in text.splitlines()
              if line.startswith("event:")]
    assert events == ["tool_call", "tool_result", "answer"]


def test_rejects_too_short_question(tools: ToolExecutor) -> None:
    assert make_client(tools, []).post("/ask", json={"question": "x"}).status_code == 422
