"""Hybrid retrieval: BM25 (exact terms, tickers, numbers) + vectors (paraphrase), fused by RRF.

Reciprocal Rank Fusion only uses ranks, so the two very different score scales never need
to be calibrated against each other.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from filings_copilot.embeddings import Embedder
from filings_copilot.filings_store import FilingsStore

RRF_K = 60  # standard constant from Cormack et al. (2009)


@dataclass(frozen=True)
class SearchFilters:
    tickers: Sequence[str] = ()
    fiscal_years: Sequence[int] = ()
    section_items: Sequence[str] = ()


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    ticker: str
    fiscal_year: int
    section_item: str
    section_title: str
    text: str
    url: str
    score: float


def _where(filters: SearchFilters) -> tuple[str, list[object]]:
    clauses: list[str] = ["TRUE"]
    params: list[object] = []
    for column, values in (
        ("c.ticker", [t.upper() for t in filters.tickers]),
        ("c.fiscal_year", list(filters.fiscal_years)),
        ("c.section_item", [s.upper() for s in filters.section_items]),
    ):
        if values:
            clauses.append(f"{column} IN ({', '.join('?' * len(values))})")
            params.extend(values)
    return " AND ".join(clauses), params


def reciprocal_rank_fusion(rankings: Sequence[Sequence[str]], k: int = RRF_K) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return scores


class HybridRetriever:
    def __init__(self, store: FilingsStore, embedder: Embedder, candidates: int = 40) -> None:
        self.store = store
        self.embedder = embedder
        self.candidates = candidates

    def _bm25(self, query: str, where: str, params: list[object]) -> list[str]:
        rows = self.store.cursor().execute(
            f"""
            SELECT chunk_id FROM (
                SELECT c.chunk_id, fts_main_chunks.match_bm25(c.chunk_id, ?) AS score
                FROM chunks c WHERE {where}
            ) WHERE score IS NOT NULL ORDER BY score DESC LIMIT ?
            """,
            [query, *params, self.candidates],
        ).fetchall()
        return [r[0] for r in rows]

    def _vector(self, query: str, where: str, params: list[object]) -> list[str]:
        vector = self.embedder.embed_query(query)
        rows = self.store.cursor().execute(
            f"""
            SELECT c.chunk_id FROM chunks c WHERE {where}
            ORDER BY array_cosine_similarity(c.embedding, ?::FLOAT[{self.store.dimension}]) DESC
            LIMIT ?
            """,
            [*params, vector, self.candidates],
        ).fetchall()
        return [r[0] for r in rows]

    def search(
        self, query: str, filters: SearchFilters | None = None, k: int = 8
    ) -> list[RetrievedChunk]:
        where, params = _where(filters or SearchFilters())
        fused = reciprocal_rank_fusion(
            [self._bm25(query, where, params), self._vector(query, where, params)]
        )
        top = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:k]
        if not top:
            return []
        ids = [chunk_id for chunk_id, _ in top]
        rows = self.store.cursor().execute(
            f"""
            SELECT c.chunk_id, c.ticker, c.fiscal_year, c.section_item, c.section_title,
                   c.text, f.url
            FROM chunks c JOIN filings f USING (accession_number)
            WHERE c.chunk_id IN ({', '.join('?' * len(ids))})
            """,
            ids,
        ).fetchall()
        by_id = {r[0]: r for r in rows}
        return [
            RetrievedChunk(*by_id[chunk_id], score=score)  # type: ignore[misc]
            for chunk_id, score in top
            if chunk_id in by_id
        ]
