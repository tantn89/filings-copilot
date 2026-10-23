"""Tool-use agent loop that yields events, so the CLI, API and evals share one code path.

We drive the loop by hand (instead of the SDK tool runner) because each step is surfaced
as an event: the UI streams tool calls live and the eval harness records the full trace.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from filings_copilot.agent_tools import TOOL_DEFINITIONS, ToolExecutor
from filings_copilot.hybrid_retriever import RetrievedChunk

SYSTEM_PROMPT = """\
You are a financial research analyst answering questions about US public companies from
their SEC 10-K filings. You have two sources:
- query_financials: audited XBRL numbers. Every figure you state must come from it.
- search_filings: the narrative text of 10-K sections. Use it for reasons, risks, strategy.

Work method: check what data exists if unsure, gather evidence with tools, then answer.
Compute derived figures (growth, margins) in SQL rather than in your head.

Answer format: lead with the direct answer in 1-3 sentences, then supporting detail.
Cite narrative claims with the passage ids from search_filings, one id per bracket,
e.g. [NVDA-2025-7-0012][NVDA-2025-1A-0003].
Format money in billions or millions with the fiscal year, e.g. "$130.5B in FY2025".
If the data does not contain the answer, say so plainly instead of guessing."""

CITATION_ID = re.compile(r"[A-Z.\-]+-\d{4}-\d{1,2}[A-C]?-\d{4}")
_BRACKET = re.compile(r"\[([^\[\]]+)\]")

EventType = Literal["tool_call", "tool_result", "answer", "error"]


@dataclass
class AgentEvent:
    type: EventType
    data: dict[str, Any]


@dataclass
class AgentResult:
    answer: str
    citations: list[RetrievedChunk]
    tool_calls: list[dict[str, Any]]
    input_tokens: int = 0  # all input, including the cached parts below
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float | None = None  # billed cost when the provider reports it (OpenRouter)
    latency_s: float = 0.0
    stop_reason: str = ""
    events: list[AgentEvent] = field(default_factory=list)


class ResearchAgent:
    def __init__(
        self,
        create_message: Callable[..., Any],
        tools: ToolExecutor,
        model: str = "claude-opus-5-5",
        effort: str = "medium",
        max_turns: int = 8,
    ) -> None:
        # Usually `anthropic.Anthropic().beta.messages.create`; tests inject a fake.
        self.create_message = create_message
        self.tools = tools
        self.model = model
        self.effort = effort
        self.max_turns = max_turns

    def _request(self, messages: list[dict[str, Any]]) -> Any:
        return self.create_message(
            model=self.model,
            max_tokens=16000,
            system=[
                {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
            ],
            tools=TOOL_DEFINITIONS,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
            # If a safety classifier declines, the API retries on a fallback model in-call.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )

    def stream(self, question: str) -> Iterator[AgentEvent]:
        """Run the loop, yielding events; the final event is 'answer' or 'error'."""
        started = time.perf_counter()
        messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
        seen: dict[str, RetrievedChunk] = {}
        tool_calls: list[dict[str, Any]] = []
        usage = {"input_tokens": 0, "output_tokens": 0,
                 "cache_read_tokens": 0, "cache_write_tokens": 0}
        reported_costs: list[float] = []

        for _ in range(self.max_turns):
            response = self._request(messages)
            cache_read = getattr(response.usage, "cache_read_input_tokens", 0) or 0
            cache_write = getattr(response.usage, "cache_creation_input_tokens", 0) or 0
            usage["input_tokens"] += response.usage.input_tokens + cache_read + cache_write
            usage["cache_read_tokens"] += cache_read
            usage["cache_write_tokens"] += cache_write
            usage["output_tokens"] += response.usage.output_tokens
            if getattr(response.usage, "cost_usd", None) is not None:
                reported_costs.append(float(response.usage.cost_usd))
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "refusal":
                yield AgentEvent("error", {"message": "The model declined this request."})
                return
            if response.stop_reason == "max_tokens":
                yield AgentEvent("error", {"message": "Response hit max_tokens."})
                return
            if response.stop_reason == "end_turn":
                text = "".join(b.text for b in response.content if b.type == "text").strip()
                cited = [seen[c] for c in cited_ids(text) if c in seen]
                yield AgentEvent("answer", {
                    "answer": text,
                    "citations": [_citation_dict(c) for c in cited],
                    "tool_calls": tool_calls,
                    "stop_reason": response.stop_reason,
                    "latency_s": round(time.perf_counter() - started, 2),
                    "cost_usd": round(sum(reported_costs), 6) if reported_costs else None,
                    **usage,
                })
                return
            if response.stop_reason != "tool_use":
                # e.g. model_context_window_exceeded: partial text is not an answer.
                yield AgentEvent("error", {
                    "message": f"The model stopped early ({response.stop_reason})."})
                return

            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                call = {"id": block.id, "name": block.name, "input": block.input}
                yield AgentEvent("tool_call", call)
                t0 = time.perf_counter()
                outcome = self.tools.run(block.name, dict(block.input))
                elapsed = round(time.perf_counter() - t0, 3)
                for chunk in outcome.citations:
                    seen[chunk.chunk_id] = chunk
                tool_calls.append({**call, "is_error": outcome.is_error, "latency_s": elapsed})
                yield AgentEvent("tool_result", {
                    "id": block.id, "name": block.name, "is_error": outcome.is_error,
                    "latency_s": elapsed, "preview": outcome.content[:400],
                })
                results.append({
                    "type": "tool_result", "tool_use_id": block.id,
                    "content": outcome.content, "is_error": outcome.is_error,
                })
            # All results of one turn go back in a single user message.
            messages.append({"role": "user", "content": results})

        yield AgentEvent("error", {"message": f"Stopped after {self.max_turns} turns."})

    def ask(self, question: str) -> AgentResult:
        events = list(self.stream(question))
        final = events[-1]
        if final.type == "error":
            return AgentResult(answer="", citations=[], tool_calls=[], events=events,
                               stop_reason=final.data["message"])
        d = final.data
        return AgentResult(
            answer=d["answer"],
            citations=[RetrievedChunk(**c) for c in d["citations"]],
            tool_calls=d["tool_calls"],
            input_tokens=d["input_tokens"],
            output_tokens=d["output_tokens"],
            cache_read_tokens=d["cache_read_tokens"],
            cache_write_tokens=d["cache_write_tokens"],
            cost_usd=d["cost_usd"],
            latency_s=d["latency_s"],
            stop_reason=d["stop_reason"],
            events=events,
        )


def cited_ids(text: str) -> list[str]:
    """Citation ids in order of first appearance.

    Accepts one id per bracket ("[A][B]") and grouped ids ("[A, B]"); a bracket counts only
    if every token in it is an id, so prose like "[1]" or "[note]" is ignored.
    """
    ids: dict[str, None] = {}
    for group in _BRACKET.findall(text):
        tokens = [t for t in re.split(r"[,;\s]+", group) if t]
        if tokens and all(CITATION_ID.fullmatch(t) for t in tokens):
            ids.update(dict.fromkeys(tokens))
    return list(ids)


def _citation_dict(chunk: RetrievedChunk) -> dict[str, Any]:
    return asdict(chunk)
