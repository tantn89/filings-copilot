"""Wire settings, store, retriever and agent together for the CLI and the API."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import anthropic

from filings_copilot.agent_tools import ToolExecutor
from filings_copilot.config import Settings
from filings_copilot.embeddings import FastEmbedEmbedder
from filings_copilot.filings_store import FilingsStore
from filings_copilot.hybrid_retriever import HybridRetriever
from filings_copilot.openai_compatible_client import OpenAICompatibleMessages
from filings_copilot.research_agent import ResearchAgent


@dataclass
class Runtime:
    store: FilingsStore
    agent: ResearchAgent


def build_runtime(settings: Settings) -> Runtime:
    if not settings.db_path.exists():
        raise FileNotFoundError(
            f"{settings.db_path} not found. Run `filings-copilot ingest` first."
        )
    embedder = FastEmbedEmbedder(settings.embedding_model)
    store = FilingsStore(settings.db_path, embedder.dimension, read_only=True)
    tools = ToolExecutor(store, HybridRetriever(store, embedder))
    agent = ResearchAgent(
        _create_message(settings),
        tools,
        model=settings.llm_model,
        effort=settings.llm_effort,
        max_turns=settings.max_agent_turns,
    )
    return Runtime(store=store, agent=agent)


def _create_message(settings: Settings) -> Callable[..., Any]:
    if settings.llm_provider == "openrouter":
        if "/" not in settings.llm_model:
            raise ValueError(
                "With FILINGS_LLM_PROVIDER=openrouter, set FILINGS_LLM_MODEL to an OpenRouter "
                "model id ('provider/model', see https://openrouter.ai/models)."
            )
        client = OpenAICompatibleMessages(settings.openrouter_api_key, settings.openrouter_base_url)
        return client.create
    # None lets the SDK fall back to its own credential chain (env var, `ant` profile).
    return anthropic.Anthropic(api_key=settings.anthropic_api_key or None).beta.messages.create
