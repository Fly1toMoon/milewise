"""search_rules: vector recall, then a small-model relevance filter (design.md §3.1)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from milewise import llm
from milewise.config import Settings, get_settings
from milewise.rag.embeddings import embed
from milewise.rag.index import RulesIndex

RECALL_K = 20
# Whole chunks go to the reranker and the model: chunks are up to MAX_CHARS of body plus
# their heading path (longest 1,016 in the 2026-10-21 index). 700 used to cut 22 of them.
SNIPPET_CHARS = 1200
# Pages are returned whole when they are short (design.md §4.13): the oneworld conditions page
# is 1,564 characters in 7 chunks, and a query often reached only one of the two chunks that
# decide an itinerary question (旅程について / 利用区間・途中降機について), flipping the
# answer. A longer page gives the hit and its neighbours. MAX_RESULT_CHARS caps the total.
PAGE_EXPAND_CHARS = 3000
MAX_RESULT_CHARS = 8000
_SENTENCE_END = "。"

RERANK_SCHEMA = {
    "type": "object",
    "properties": {"relevant": {"type": "array", "items": {"type": "integer"}}},
    "required": ["relevant"],
    "additionalProperties": False,
}
RERANK_INSTRUCTIONS = (
    "You are given a question and numbered excerpts from airline award-ticket rule pages. "
    "Return the numbers of the excerpts that contain information needed to answer the "
    "question, most useful first. Each excerpt starts with its heading path, which says "
    "which award and which situation it applies to (e.g. a weather or equipment-trouble "
    "case). When excerpts for the general rule and for special cases are both relevant, "
    "include the general-rule ones; never return only special-case excerpts if a "
    "general-rule excerpt answers the question. Return an empty list if none of them do. "
    "Do not answer."
)


@dataclass
class RulesResult:
    status: str  # ok | no_evidence
    chunks: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)


def recall(
    index: RulesIndex,
    query: str,
    airline: str | None,
    settings: Settings,
    k: int = RECALL_K,
    award: str | None = None,
) -> list[dict[str, Any]]:
    # Embed the query with the model the index was built with, whatever the settings say.
    [vec] = embed([query], settings, model=index.manifest["embedding_model"], kind="query")
    return index.recall(vec, airline, k, award)


TRANSLATE_SCHEMA = {
    "type": "object",
    "properties": {"ja": {"type": "string"}},
    "required": ["ja"],
    "additionalProperties": False,
}
TRANSLATE_INSTRUCTIONS = (
    "Translate the user's question about airline award tickets into natural Japanese, using "
    "the wording ANA and JAL use on their Japanese rule pages (an award ticket is "
    "特典航空券). Keep airline names, places, dates and numbers. Do not answer the question."
)
_KANA = re.compile(r"[\u3040-\u30ff]")


def to_japanese(query: str, settings: Settings) -> tuple[str, dict[str, int]]:
    """The rules pages are Japanese: retrieving with a Japanese query avoids cross-lingual
    embedding gaps (退票 vs 払い戻し, 候补 vs 空席待ち; design.md §9.16). Japanese passes through."""
    if _KANA.search(query):
        return query, {}
    out, usage = llm.structured(
        settings.rerank_model,
        name="translate",
        instructions=TRANSLATE_INSTRUCTIONS,
        prompt=query,
        schema=TRANSLATE_SCHEMA,
        s=settings,
        max_tokens=300,
    )
    return str(out.get("ja") or query), usage


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


def _navigation_only(row: dict[str, Any]) -> bool:
    """A chunk of link and menu text (「重要なお知らせ」, the page footer): no sentence in it."""
    body = row["text"].split("\n", 1)[1] if "\n" in row["text"] else row["text"]
    return _SENTENCE_END not in body


def expand_pages(
    index: RulesIndex,
    hits: list[dict[str, Any]],
    page_chars: int = PAGE_EXPAND_CHARS,
    max_chars: int = MAX_RESULT_CHARS,
) -> list[dict[str, Any]]:
    """The reranked hits with the rest of their page: a whole page when it is at most
    `page_chars` long, else each hit with the chunk before and after it. Filled in rerank
    order, each page in page order, until `max_chars`; navigation-only chunks are skipped
    unless they are a hit. Rows that are not hits carry no score."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    total = 0
    for hit in hits:
        if hit["chunk_id"] in seen:
            continue
        page = index.page(hit["source_url"]) or [hit]
        size = sum(len(r["text"][:SNIPPET_CHARS]) for r in page if not _navigation_only(r))
        if size <= page_chars:
            group = page
        else:
            at = next((i for i, r in enumerate(page) if r["chunk_id"] == hit["chunk_id"]), None)
            group = [hit] if at is None else page[max(0, at - 1) : at + 2]
        group = [
            {**r, "score": hit["score"]} if r["chunk_id"] == hit["chunk_id"] else r
            for r in group
            if r["chunk_id"] not in seen
            and (r["chunk_id"] == hit["chunk_id"] or not _navigation_only(r))
        ]
        bodies = {r.get("body_sha256") for r in out}
        for r in group:
            n = len(r["text"][:SNIPPET_CHARS])
            if total + n > max_chars:
                if r["chunk_id"] == hit["chunk_id"]:
                    return out  # not even the hit fits: nothing ranked lower may jump ahead
                continue
            if r["chunk_id"] != hit["chunk_id"] and r.get("body_sha256") in bodies:
                continue  # the same notice printed twice on one page
            seen.add(r["chunk_id"])
            out.append(r)
            total += n
    return out


def search_rules(
    index: RulesIndex,
    question: str,
    airline: str | None = None,
    k: int = 6,
    settings: Settings | None = None,
    award_type: str | None = None,
) -> RulesResult:
    s = settings or get_settings()
    rows = recall(index, question, airline, s, award=award_type)
    if not rows:
        return RulesResult("no_evidence")
    picked, usage = rerank(question, rows, s)
    expanded = expand_pages(index, [rows[i] for i in picked[:k]])
    chunks = [
        {
            "chunk_id": r["chunk_id"],
            "title": r["title"],
            "url": r["source_url"],
            "airline": r["airline"],
            "award_type": r.get("award", "general"),
            # the situation the excerpt covers, e.g. "悪天候や機材故障などが理由の…" (§9.18)
            "section": r.get("section", ""),
            "fetched_date": r["fetched_date"],
            # a reranked hit; None for the rest of its page added around it
            "score": round(float(r["score"]), 3) if r.get("score") is not None else None,
            "text": r["text"][:SNIPPET_CHARS],
        }
        for r in expanded
    ]
    return RulesResult("ok" if chunks else "no_evidence", chunks, usage)
