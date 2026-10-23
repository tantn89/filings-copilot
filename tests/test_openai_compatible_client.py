from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from filings_copilot.agent_tools import TOOL_DEFINITIONS, ToolExecutor
from filings_copilot.openai_compatible_client import OpenAICompatibleMessages, to_chat_messages
from filings_copilot.research_agent import ResearchAgent


def chat_reply(content: str | None = None, tool_calls: list[dict[str, Any]] | None = None,
               finish: str = "stop", cost: float = 0.001) -> dict[str, Any]:
    return {
        "model": "openai/gpt-test",
        "choices": [{"finish_reason": finish,
                     "message": {"role": "assistant", "content": content,
                                 "tool_calls": tool_calls}}],
        "usage": {"prompt_tokens": 50, "completion_tokens": 10, "cost": cost},
    }


class FakeChatServer:
    def __init__(self, replies: list[dict[str, Any]]) -> None:
        self.replies = list(replies)
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer key"
        self.bodies.append(json.loads(request.content))
        return httpx.Response(200, json=self.replies.pop(0))


def test_history_translation_covers_tool_calls_and_results() -> None:
    chat = to_chat_messages("SYS", [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "checking"},
            {"type": "tool_use", "id": "c1", "name": "list_companies", "input": {}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "c1", "content": "[]", "is_error": False}]},
    ])
    assert chat == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "checking", "tool_calls": [{
            "id": "c1", "type": "function",
            "function": {"name": "list_companies", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "[]"},
    ]


def test_agent_runs_end_to_end_through_the_adapter(tools: ToolExecutor) -> None:
    server = FakeChatServer([
        chat_reply(tool_calls=[{"id": "c1", "type": "function", "function": {
            "name": "query_financials",
            "arguments": json.dumps({"sql": "SELECT value FROM annual_financials "
                                            "WHERE fiscal_year = 2025"})}}],
                   finish="tool_calls"),
        chat_reply("Revenue was $130.5B in FY2025."),
    ])
    client = OpenAICompatibleMessages("key", "https://example.test/v1",
                                      transport=httpx.MockTransport(server))
    result = ResearchAgent(client.create, tools, model="openai/gpt-test").ask("Revenue?")

    assert result.answer == "Revenue was $130.5B in FY2025."
    assert result.cost_usd == pytest.approx(0.002)
    assert result.input_tokens == 100
    first = server.bodies[0]
    assert first["model"] == "openai/gpt-test"
    assert {t["function"]["name"] for t in first["tools"]} == {t["name"] for t in TOOL_DEFINITIONS}
    assert "thinking" not in first and "fallbacks" not in first
    tool_message = server.bodies[1]["messages"][-1]
    assert tool_message["role"] == "tool" and "130497000000.0" in tool_message["content"]


def test_malformed_tool_arguments_become_a_tool_error(tools: ToolExecutor) -> None:
    server = FakeChatServer([
        chat_reply(tool_calls=[{"id": "c1", "type": "function", "function": {
            "name": "query_financials", "arguments": "{not json"}}], finish="tool_calls"),
        chat_reply("Sorry."),
    ])
    client = OpenAICompatibleMessages("key", "https://example.test/v1",
                                      transport=httpx.MockTransport(server))
    events = list(ResearchAgent(client.create, tools).stream("x"))
    assert events[1].data["is_error"] is True


def test_provider_error_inside_200_response_raises() -> None:
    server = FakeChatServer([{"error": {"message": "upstream overloaded"}}])
    client = OpenAICompatibleMessages("key", "https://example.test/v1",
                                      transport=httpx.MockTransport(server))
    with pytest.raises(RuntimeError, match="upstream overloaded"):
        client.create(model="m", messages=[{"role": "user", "content": "hi"}])


def test_requires_api_key() -> None:
    with pytest.raises(ValueError):
        OpenAICompatibleMessages("")


def test_tool_calls_win_over_finish_reason_and_ids_are_made_unique() -> None:
    from filings_copilot.openai_compatible_client import from_chat_response

    def call(id_: str, args: str) -> dict[str, Any]:
        return {"id": id_, "type": "function",
                "function": {"name": "list_companies", "arguments": args}}

    reply = from_chat_response(chat_reply(finish="stop", tool_calls=[
        call("", "{}"), call("dup", "[]"), call("dup", "{}")]))
    assert reply.stop_reason == "tool_use"
    ids = [b.id for b in reply.content if b.type == "tool_use"]
    assert len(set(ids)) == 3 and all(ids)
    assert reply.content[1].input == {"_invalid_json": "[]"}  # a JSON list is not tool input
