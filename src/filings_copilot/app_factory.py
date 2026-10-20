"""Wire settings, store, retriever and agent together for the CLI and the API."""

from __future__ import annotations

from dataclasses import dataclass

import anthropic

from filings_copilot.agent_tools import ToolExecutor
from filings_copilot.config import Settings
from filings_copilot.embeddings import FastEmbedEmbedder
from filings_copilot.filings_store import FilingsStore
from filings_copilot.hybrid_retriever import HybridRetriever
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
        anthropic.Anthropic().beta.messages.create,
        tools,
        model=settings.llm_model,
        effort=settings.llm_effort,
        max_turns=settings.max_agent_turns,
    )
    return Runtime(store=store, agent=agent)
