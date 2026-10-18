"""search_rules: vector recall, then a small-model relevance filter (design.md §3.1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from milewise import llm
from milewise.config import Settings, get_settings
from milewise.rag.embeddings import embed
from milewise.rag.index import RulesIndex

RECALL_K = 20
SNIPPET_CHARS = 700

RERANK_SCHEMA = {
    "type": "object",
    "properties": {"relevant": {"type": "array", "items": {"type": "integer"}}},
    "required": ["relevant"],
    "additionalProperties": False,
}
RERANK_INSTRUCTIONS = (
    "You are given a question and numbered excerpts from airline award-ticket rule pages. "
    "Return the numbers of the excerpts that contain information needed to answer the "
    "question, most useful first. Return an empty list if none of them do. Do not answer."
)


@dataclass
class RulesResult:
    status: str  # ok | no_evidence
    chunks: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)


def recall(
    index: RulesIndex, query: str, airline: str | None, settings: Settings, k: int = RECALL_K
) -> list[dict[str, Any]]:
    # Embed the query with the model the index was built with, whatever the settings say.
    [vec] = embed([query], settings, model=index.manifest["embedding_model"])
    return index.recall(vec, airline, k)


def rerank(
    question: str, rows: list[dict[str, Any]], settings: Settings
) -> tuple[list[int], dict[str, int]]:
    excerpts = "\n\n".join(f"[{i}] {r['text'][:SNIPPET_CHARS]}" for i, r in enumerate(rows))
    out, usage = llm.structured(
        settings.rerank_model,
        name="rerank",
        instructions=RERANK_INSTRUCTIONS,
        prompt=f"Question: {question}\n\nExcerpts:\n{excerpts}",
        schema=RERANK_SCHEMA,
        s=settings,
        max_tokens=1000,
    )
    picked = [i for i in out.get("relevant", []) if isinstance(i, int) and 0 <= i < len(rows)]
    return list(dict.fromkeys(picked)), usage


def search_rules(
    index: RulesIndex,
    question: str,
    airline: str | None = None,
    k: int = 5,
    settings: Settings | None = None,
) -> RulesResult:
    s = settings or get_settings()
    rows = recall(index, question, airline, s)
    if not rows:
        return RulesResult("no_evidence")
    picked, usage = rerank(question, rows, s)
    chunks = [
        {
            "title": rows[i]["title"],
            "url": rows[i]["source_url"],
            "airline": rows[i]["airline"],
            "fetched_date": rows[i]["fetched_date"],
            "score": round(float(rows[i]["score"]), 3),
            "text": rows[i]["text"][:SNIPPET_CHARS],
        }
        for i in picked[:k]
    ]
    return RulesResult("ok" if chunks else "no_evidence", chunks, usage)
