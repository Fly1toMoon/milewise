"""search_rules: vector recall, then a small-model relevance filter (design.md §3.1)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI

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
    [vec] = embed([query], settings)
    return index.recall(vec, airline, k)


def rerank(
    question: str, rows: list[dict[str, Any]], settings: Settings
) -> tuple[list[int], dict[str, int]]:
    client = OpenAI(api_key=settings.openai_api_key or None, max_retries=2, timeout=60)
    excerpts = "\n\n".join(f"[{i}] {r['text'][:SNIPPET_CHARS]}" for i, r in enumerate(rows))
    r = client.responses.create(
        model=settings.openai_model,
        instructions=RERANK_INSTRUCTIONS,
        input=f"Question: {question}\n\nExcerpts:\n{excerpts}",
        text={
            "format": {
                "type": "json_schema",
                "name": "rerank",
                "schema": RERANK_SCHEMA,
                "strict": True,
            }
        },
        reasoning={"effort": "low"},
        max_output_tokens=1000,
    )
    picked = [i for i in json.loads(r.output_text)["relevant"] if 0 <= i < len(rows)]
    usage = (
        {"input_tokens": r.usage.input_tokens, "output_tokens": r.usage.output_tokens}
        if r.usage
        else {}
    )
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
