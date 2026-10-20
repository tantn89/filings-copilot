"""Run the agent on any model behind an OpenAI-compatible chat API (e.g. OpenRouter).

The agent speaks the Anthropic Messages shape. This adapter exposes the same `create(...)`
call: it translates tools and history to chat-completions format, and translates the reply
back into Anthropic-style content blocks, so the agent loop, tools and evals stay unchanged.
Anthropic-only options (thinking, effort, prompt caching, refusal fallbacks) are dropped.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any

import httpx

_STOP_REASONS = {
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "length": "max_tokens",
    "content_filter": "refusal",
    "stop": "end_turn",
}


def _block_field(block: Any, name: str) -> Any:
    """Content blocks are dicts (built by us) or objects (returned by an SDK / this adapter)."""
    return block.get(name) if isinstance(block, dict) else getattr(block, name)


def _system_text(system: Any) -> str:
    if isinstance(system, str):
        return system
    return "\n\n".join(_block_field(b, "text") for b in system or [])


def to_chat_messages(system: Any, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chat: list[dict[str, Any]] = []
    if system:
        chat.append({"role": "system", "content": _system_text(system)})
    for message in messages:
        content = message["content"]
        if isinstance(content, str):
            chat.append({"role": message["role"], "content": content})
            continue
        if message["role"] == "assistant":
            text = "".join(
                _block_field(b, "text") for b in content if _block_field(b, "type") == "text"
            )
            tool_calls = [
                {
                    "id": _block_field(b, "id"),
                    "type": "function",
                    "function": {
                        "name": _block_field(b, "name"),
                        "arguments": json.dumps(_block_field(b, "input")),
                    },
                }
                for b in content
                if _block_field(b, "type") == "tool_use"
            ]
            entry: dict[str, Any] = {"role": "assistant", "content": text or None}
            if tool_calls:
                entry["tool_calls"] = tool_calls
            chat.append(entry)
        else:
            for block in content:
                if _block_field(block, "type") == "tool_result":
                    chat.append({
                        "role": "tool",
                        "tool_call_id": _block_field(block, "tool_use_id"),
                        "content": _block_field(block, "content"),
                    })
                elif _block_field(block, "type") == "text":
                    chat.append({"role": "user", "content": _block_field(block, "text")})
    return chat


def to_chat_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]


def from_chat_response(payload: dict[str, Any]) -> SimpleNamespace:
    choice = payload["choices"][0]
    message = choice["message"]
    content: list[SimpleNamespace] = []
    if message.get("content"):
        content.append(SimpleNamespace(type="text", text=message["content"]))
    for call in message.get("tool_calls") or []:
        raw = call["function"].get("arguments") or "{}"
        try:
            tool_input = json.loads(raw)
        except json.JSONDecodeError:
            # Pass the bad payload through; the tool executor rejects it and the model
            # sees an error result it can correct on the next turn.
            tool_input = {"_invalid_json": raw}
        content.append(SimpleNamespace(
            type="tool_use", id=call["id"], name=call["function"]["name"], input=tool_input
        ))
    usage = payload.get("usage") or {}
    return SimpleNamespace(
        content=content,
        stop_reason=_STOP_REASONS.get(choice.get("finish_reason") or "stop", "end_turn"),
        model=payload.get("model"),
        usage=SimpleNamespace(
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            cost_usd=usage.get("cost"),
        ),
    )


class OpenAICompatibleMessages:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://openrouter.ai/api/v1",
        transport: httpx.BaseTransport | None = None,
        timeout: float = 300.0,
    ) -> None:
        if not api_key:
            raise ValueError("Missing API key for the OpenAI-compatible provider.")
        self._http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}", "X-Title": "filings-copilot"},
            timeout=timeout,
            transport=transport,
        )

    def create(self, **kwargs: Any) -> SimpleNamespace:
        body = {
            "model": kwargs["model"],
            "max_tokens": kwargs.get("max_tokens", 16000),
            "messages": to_chat_messages(kwargs.get("system"), kwargs["messages"]),
            "tools": to_chat_tools(kwargs.get("tools", [])),
            "usage": {"include": True},  # OpenRouter: return the billed cost per call
        }
        for attempt in range(4):
            response = self._http.post("/chat/completions", json=body)
            if response.status_code in (429, 500, 502, 503) and attempt < 3:
                time.sleep(2**attempt)
                continue
            response.raise_for_status()
            payload = response.json()
            # OpenRouter can report upstream provider failures inside a 200 response.
            if "error" in payload or not payload.get("choices"):
                error = payload.get("error") or {}
                raise RuntimeError(f"Provider error: {error.get('message', payload)}")
            return from_chat_response(payload)
        raise AssertionError("unreachable")
