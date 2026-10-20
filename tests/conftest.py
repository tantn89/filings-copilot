from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from filings_copilot.agent_tools import ToolExecutor
from filings_copilot.edgar_client import Company, FilingRef
from filings_copilot.embeddings import HashingEmbedder
from filings_copilot.filings_store import ChunkRecord, FilingsStore
from filings_copilot.financial_facts_loader import MetricRow
from filings_copilot.hybrid_retriever import HybridRetriever

PASSAGES = [
    ("7", "Data Center revenue grew due to strong demand for Hopper GPUs used in AI training."),
    ("7", "Gross margin increased primarily driven by a higher mix of Data Center revenue."),
    ("1A", "Export controls restricting sales of chips to China could harm our business."),
    ("1A", "We depend on TSMC to manufacture our wafers; supply disruption is a key risk."),
    ("1", "We pioneered accelerated computing and the GPU."),
]


@pytest.fixture
def embedder() -> HashingEmbedder:
    return HashingEmbedder(dimension=128)


@pytest.fixture
def store(embedder: HashingEmbedder) -> Iterator[FilingsStore]:
    s = FilingsStore(":memory:", embedder.dimension)
    s.upsert_company(Company(1045810, "NVDA", "NVIDIA CORP"))
    ref = FilingRef(1045810, "NVDA", "10-K", "0001045810-25-000023", "2025-02-26",
                    "2025-01-26", "nvda-20250126.htm")
    s.upsert_filing(ref)
    vectors = embedder.embed_documents([text for _, text in PASSAGES])
    s.replace_chunks(ref.accession_number, [
        ChunkRecord(f"NVDA-2025-{item}-{i:04d}", ref.accession_number, "NVDA", 2025, item,
                    "Section", i, text, vec)
        for i, ((item, text), vec) in enumerate(zip(PASSAGES, vectors, strict=True))
    ])
    s.replace_metrics("NVDA", [
        MetricRow(1045810, "NVDA", "revenue", 2025, date(2024, 1, 29), date(2025, 1, 26),
                  130_497e6, "USD", "Revenues", ref.accession_number, date(2025, 2, 26)),
        MetricRow(1045810, "NVDA", "revenue", 2024, date(2023, 1, 30), date(2024, 1, 28),
                  60_922e6, "USD", "Revenues", ref.accession_number, date(2025, 2, 26)),
    ])
    s.rebuild_text_index()
    yield s
    s.close()


@pytest.fixture
def tools(store: FilingsStore, embedder: HashingEmbedder) -> ToolExecutor:
    return ToolExecutor(store, HybridRetriever(store, embedder))


def text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def tool_use_block(id_: str, name: str, tool_input: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=tool_input)


def fake_response(content: list[SimpleNamespace], stop_reason: str) -> SimpleNamespace:
    usage = SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=0,
                            cache_creation_input_tokens=0)
    return SimpleNamespace(content=content, stop_reason=stop_reason, usage=usage)


class ScriptedMessagesAPI:
    """Stands in for `client.beta.messages`: returns canned responses in order."""

    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> SimpleNamespace:
        # Snapshot messages: the agent keeps appending to the same list.
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)
