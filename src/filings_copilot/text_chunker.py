"""Paragraph-aware word-window chunking."""

from __future__ import annotations


def chunk_text(text: str, size_words: int = 220, overlap_words: int = 40) -> list[str]:
    """Pack whole paragraphs into ~size_words chunks; split oversized paragraphs by words.

    Consecutive chunks share `overlap_words` trailing words so a fact that straddles a
    boundary is still retrievable from one chunk.
    """
    if overlap_words >= size_words:
        raise ValueError("overlap_words must be smaller than size_words")

    words_per_paragraph = [p.split() for p in text.split("\n") if p.strip()]
    chunks: list[str] = []
    current: list[str] = []

    def flush() -> None:
        nonlocal current
        if current:
            chunks.append(" ".join(current))
            current = current[-overlap_words:] if overlap_words else []

    for words in words_per_paragraph:
        if len(current) + len(words) > size_words and len(current) > overlap_words:
            flush()
        while len(current) + len(words) > size_words:
            room = size_words - len(current)
            current.extend(words[:room])
            words = words[room:]
            flush()
        current.extend(words)
    if len(current) > overlap_words or not chunks:
        flush()
    return [c for c in chunks if c]
