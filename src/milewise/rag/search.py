"""search_rules: vector recall, then a small-model relevance filter (design.md §3.1)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI

from milewise.config import Settings, get_settings
from milewise.db import Db
from milewise.rag.embeddings import embed, to_pgvector

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


def recall_by_vector(
    db: Db, vec: list[float], airline: str | None, k: int = RECALL_K
) -> list[dict[str, Any]]:
    """Nearest chunks, one per distinct body text.

    The same notice is printed on many pages; without this, copies crowd out other
    evidence, and which page "owns" the copy would depend on ingestion order and
    concurrency (design.md §9.10). The inner query still uses the HNSW index.
    """
    return db.query(
        "WITH candidates AS ("
        "  SELECT c.chunk_id, c.text, c.body_sha256, d.title, d.source_url, d.airline,"
        "   d.doc_type, d.fetched_at, c.embedding <=> CAST(:q AS vector) AS dist"
        "  FROM rag.chunks c JOIN rag.documents d USING (doc_id)"
        "  WHERE d.is_current AND (:airline = '' OR d.airline = :airline)"
        "  ORDER BY c.embedding <=> CAST(:q AS vector) LIMIT :pool"
        "), distinct_bodies AS ("
        "  SELECT DISTINCT ON (body_sha256) * FROM candidates"
        "  ORDER BY body_sha256, dist, source_url"
        ")"
        " SELECT chunk_id, text, title, source_url, airline, doc_type,"
        "  to_char(fetched_at AT TIME ZONE 'Asia/Tokyo', 'YYYY-MM-DD') AS fetched_date,"
        "  1 - dist AS score"
        " FROM distinct_bodies ORDER BY dist, source_url LIMIT :k",
        {"q": to_pgvector(vec), "airline": airline or "", "k": k, "pool": 3 * k},
    )


def recall(db: Db, query: str, airline: str | None, settings: Settings) -> list[dict[str, Any]]:
    [vec] = embed([query], settings)
    return recall_by_vector(db, vec, airline)


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
    db: Db, question: str, airline: str | None = None, k: int = 5, settings: Settings | None = None
) -> RulesResult:
    s = settings or get_settings()
    rows = recall(db, question, airline, s)
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
