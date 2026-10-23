from __future__ import annotations

from conftest import ScriptedMessagesAPI, fake_response, text_block, tool_use_block
from filings_copilot.agent_tools import ToolExecutor
from filings_copilot.research_agent import ResearchAgent, cited_ids


def test_agent_runs_tools_and_collects_citations(tools: ToolExecutor) -> None:
    api = ScriptedMessagesAPI([
        fake_response([
            tool_use_block("t1", "query_financials", {
                "sql": "SELECT value FROM annual_financials WHERE metric='revenue' "
                       "AND fiscal_year=2025"}),
            tool_use_block("t2", "search_filings", {
                "query": "why revenue grew", "tickers": ["NVDA"], "fiscal_years": [2025],
                "section_items": ["7"]}),
        ], "tool_use"),
        fake_response([text_block(
            "NVIDIA revenue was $130.5B in FY2025, driven by Data Center demand "
            "[NVDA-2025-7-0000]. Unknown cite [NVDA-2025-7-9999].")], "end_turn"),
    ])
    result = ResearchAgent(api.create, tools).ask("How much did NVIDIA earn and why?")

    assert result.answer.startswith("NVIDIA revenue was $130.5B")
    assert [c.chunk_id for c in result.citations] == ["NVDA-2025-7-0000"]
    assert [c["name"] for c in result.tool_calls] == ["query_financials", "search_filings"]
    assert result.input_tokens == 200 and result.output_tokens == 40

    # Both tool results return together in one user message, matched by id.
    second = api.requests[1]["messages"]
    results = second[-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"]
    assert "130497000000.0" in results[0]["content"]


def test_agent_requests_use_adaptive_thinking_caching_and_fallbacks(tools: ToolExecutor) -> None:
    api = ScriptedMessagesAPI([fake_response([text_block("ok")], "end_turn")])
    ResearchAgent(api.create, tools, effort="high").ask("hi")
    request = api.requests[0]
    assert request["model"] == "claude-opus-5-5"
    assert request["thinking"] == {"type": "adaptive"}
    assert request["output_config"] == {"effort": "high"}
    assert request["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert request["fallbacks"] == "default"


def test_agent_surfaces_tool_errors_to_the_model(tools: ToolExecutor) -> None:
    api = ScriptedMessagesAPI([
        fake_response([tool_use_block("t1", "query_financials", {"sql": "DROP TABLE x"})],
                      "tool_use"),
        fake_response([text_block("I could not run that query.")], "end_turn"),
    ])
    events = list(ResearchAgent(api.create, tools).stream("drop it"))
    assert [e.type for e in events] == ["tool_call", "tool_result", "answer"]
    assert events[1].data["is_error"] is True
    assert api.requests[1]["messages"][-1]["content"][0]["is_error"] is True


def test_agent_handles_refusal_and_turn_limit(tools: ToolExecutor) -> None:
    refused = ResearchAgent(ScriptedMessagesAPI([fake_response([], "refusal")]).create, tools)
    assert refused.ask("x").answer == ""

    looping = ScriptedMessagesAPI([
        fake_response([tool_use_block(f"t{i}", "list_companies", {})], "tool_use")
        for i in range(3)
    ])
    events = list(ResearchAgent(looping.create, tools, max_turns=3).stream("loop"))
    assert events[-1].type == "error" and "3 turns" in events[-1].data["message"]


def test_cited_ids_accepts_grouped_and_separate_brackets() -> None:
    text = ("Margins rose [NVDA-2025-7-0001, NVDA-2025-7-0000]; risks remain [NVDA-2025-1A-0002]"
            "[NVDA-2025-7-0001]. See note [1] and [not an id, NVDA-2025-7-0003].")
    assert cited_ids(text) == ["NVDA-2025-7-0001", "NVDA-2025-7-0000", "NVDA-2025-1A-0002"]


def test_non_final_stop_reasons_are_errors_not_answers(tools: ToolExecutor) -> None:
    api = ScriptedMessagesAPI([
        fake_response([text_block("Partial text")], "model_context_window_exceeded")])
    events = list(ResearchAgent(api.create, tools).stream("q"))
    assert [e.type for e in events] == ["error"]
    assert "model_context_window_exceeded" in events[0].data["message"]


def test_cache_tokens_are_tracked_separately(tools: ToolExecutor) -> None:
    response = fake_response([text_block("ok")], "end_turn")
    response.usage.cache_read_input_tokens = 900
    response.usage.cache_creation_input_tokens = 50
    result = ResearchAgent(ScriptedMessagesAPI([response]).create, tools).ask("q")
    assert (result.input_tokens, result.cache_read_tokens, result.cache_write_tokens) == (
        1050, 900, 50)
