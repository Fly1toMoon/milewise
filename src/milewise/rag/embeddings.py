"""Text embeddings for the rules index and for queries (provider: milewise.llm)."""

from __future__ import annotations

from collections.abc import Sequence

from milewise import llm
from milewise.config import Settings


def embed(
    texts: Sequence[str],
    settings: Settings | None = None,
    model: str | None = None,
    kind: str = "document",
) -> list[list[float]]:
    return llm.embed(texts, model, settings, kind=kind)
