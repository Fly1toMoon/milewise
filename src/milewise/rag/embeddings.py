"""OpenAI embeddings, shortened to the dimension the vector column uses."""

from __future__ import annotations

from collections.abc import Sequence

from openai import OpenAI

from milewise.config import Settings, get_settings

BATCH = 64


def embed(texts: Sequence[str], settings: Settings | None = None) -> list[list[float]]:
    s = settings or get_settings()
    client = OpenAI(api_key=s.openai_api_key or None, max_retries=3, timeout=60)
    out: list[list[float]] = []
    for i in range(0, len(texts), BATCH):
        r = client.embeddings.create(
            model=s.openai_embedding_model,
            input=list(texts[i : i + BATCH]),
            dimensions=s.embedding_dimensions,
        )
        out.extend(d.embedding for d in sorted(r.data, key=lambda d: d.index))
    return out
