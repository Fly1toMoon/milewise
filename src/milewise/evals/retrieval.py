"""Retrieval eval: vector recall alone, before the reranker (design.md §4.4, §9.16).

    milewise eval-retrieval                                   # the index in build/index
    milewise eval-retrieval --embedding-model openai:text-embedding-3-large

With another embedding model, the same raw pages are re-indexed into build/index-<model>
(embeddings cached there), so models are compared on identical chunks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from milewise.config import Settings, get_settings
from milewise.rag.index import RulesIndex, build_index, default_index_dir
from milewise.rag.search import RECALL_K, recall

QUERIES = Path(__file__).resolve().parents[3] / "eval" / "retrieval.yaml"
KS = (1, 5, RECALL_K)


@dataclass
class QueryResult:
    id: str
    lang: str
    rank: int | None  # 1-based rank of the first chunk holding the answer, None = missed


def index_for(model: str | None, raw_dir: Path, s: Settings) -> RulesIndex:
    if not model:
        return RulesIndex.load()
    out = default_index_dir().parent / f"index-{re.sub(r'[^a-z0-9]+', '-', model.lower())}"
    build_index(raw_dir, out, cache_dir=out if out.exists() else None, settings=_with(s, model))
    return RulesIndex.load(out)


def _with(s: Settings, model: str) -> Settings:
    return s.model_copy(update={"embedding_model": model})


def run(model: str | None = None, raw_dir: Path = Path("raw")) -> dict[str, Any]:
    s = get_settings()
    index = index_for(model, raw_dir, s)
    queries = yaml.safe_load(QUERIES.read_text(encoding="utf-8"))["queries"]
    results = []
    for q in queries:
        rows = recall(index, q["q"], q.get("airline"), s, k=RECALL_K)
        rank = next(
            (
                i + 1
                for i, r in enumerate(rows)
                if q["url"] in r["source_url"] and q["phrase"] in r["text"]
            ),
            None,
        )
        results.append(QueryResult(q["id"], q["lang"], rank))
    n = len(results)
    summary: dict[str, Any] = {
        "embedding_model": index.manifest["embedding_model"],
        "n": n,
        **{f"hit@{k}": round(sum(1 for r in results if r.rank and r.rank <= k) / n, 3) for k in KS},
        "mrr": round(sum(1 / r.rank for r in results if r.rank) / n, 3),
        "by_lang": {
            lang: round(
                sum(1 for r in results if r.lang == lang and r.rank and r.rank <= 5)
                / sum(1 for r in results if r.lang == lang),
                3,
            )
            for lang in sorted({r.lang for r in results})
        },
        "missed@5": [r.id for r in results if not r.rank or r.rank > 5],
    }
    return {"summary": summary, "results": [r.__dict__ for r in results]}
