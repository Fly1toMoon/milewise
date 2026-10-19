"""Retrieval eval: vector recall alone, before the reranker (design.md §4.4, §9.16).

    milewise eval-retrieval                                   # the index in build/index
    milewise eval-retrieval --embedding-model openai:text-embedding-3-large
    milewise eval-retrieval --translate      # translate non-Japanese queries first

With another embedding model, the same raw pages are re-indexed into build/index-<model>
(embeddings cached there), so models are compared on identical chunks.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from milewise.config import Settings, get_settings
from milewise.rag.index import RulesIndex, build_index, default_index_dir
from milewise.rag.search import RECALL_K, recall, to_japanese

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


TRANSLATIONS = default_index_dir().parent / "retrieval-translations.json"


def _translations(queries: list[dict[str, Any]], s: Settings) -> dict[str, str]:
    """Translate once and reuse, so every embedding model sees identical Japanese queries."""
    cached: dict[str, str] = (
        json.loads(TRANSLATIONS.read_text(encoding="utf-8")) if TRANSLATIONS.exists() else {}
    )
    key = s.rerank_model
    for q in queries:
        if f"{key}|{q['q']}" not in cached:
            cached[f"{key}|{q['q']}"] = to_japanese(q["q"], s)[0]
    TRANSLATIONS.parent.mkdir(parents=True, exist_ok=True)
    TRANSLATIONS.write_text(json.dumps(cached, ensure_ascii=False, indent=1), encoding="utf-8")
    return {q["q"]: cached[f"{key}|{q['q']}"] for q in queries}


def run(
    model: str | None = None, raw_dir: Path = Path("raw"), translate: bool = False
) -> dict[str, Any]:
    s = get_settings()
    index = index_for(model, raw_dir, s)
    queries = yaml.safe_load(QUERIES.read_text(encoding="utf-8"))["queries"]
    ja = _translations(queries, s) if translate else {}
    results = []
    for q in queries:
        rows = recall(index, ja.get(q["q"], q["q"]), q.get("airline"), s, k=RECALL_K)
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
        "translated_to_ja": translate,
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
