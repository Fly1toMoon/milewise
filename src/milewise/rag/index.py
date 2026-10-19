"""Rules index built at image build time and searched in memory (design.md §4.6).

    milewise build-index --raw raw --out build/index [--cache <previous index dir>]

Output (the directory is copied into the image):
  chunks.jsonl     one chunk per line: text, title, URL, airline, body hash, embedding key
  embeddings.npy   float32 (n, dims), L2-normalised, row i = line i of chunks.jsonl
  manifest.json    content hash, chunker/embedding versions, per-page provenance

The newest raw copy of every `kind: rules` page in data/sources.yaml is used. Embeddings
are keyed by sha256(model|dims|text) and reused from --cache, so rebuilding after a
one-page change calls the embeddings API for that page only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from milewise.config import Settings, data_dir, get_settings
from milewise.rag.chunking import CHUNKER_VERSION, Chunk, chunk_sections, extract_sections

EmbedFn = Callable[[Sequence[str]], list[list[float]]]
INDEX_SCHEMA = 1
_TS = re.compile(r"_(\d{8}T\d{6})\.html$")
_REPO_ROOT = Path(__file__).resolve().parents[3]


def default_index_dir() -> Path:
    return Path(os.environ.get("MILEWISE_INDEX_DIR", _REPO_ROOT / "build" / "index"))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _body(chunk: Chunk) -> str:
    return chunk.text.split("\n", 1)[1] if "\n" in chunk.text else chunk.text


def award_of(source: dict[str, Any]) -> str:
    """own | partner | general: which award type a page's rules are about (§9.18).

    The oneworld / JMB-partner / ANA-partner pages reuse the own-award pages' wording
    almost verbatim, with opposite rules (JAL partner awards can be changed after
    ticketing, JAL's own cannot), so retrieval must not mix them up.
    """
    if source.get("doc_type") == "partner":
        return "partner"
    if "-intl-award-" in source["id"]:
        return "own"
    return "general"


def embedding_key(model: str, dims: int, text: str) -> str:
    return _sha(f"{model}|{dims}|{text}")


def newest_raw(raw_dir: Path, source: dict[str, Any]) -> Path | None:
    files = sorted((raw_dir / source["airline"].lower()).glob(f"{source['id']}_*.html"))
    return files[-1] if files else None


def _normalise(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    return (m / np.where(norms == 0, 1, norms)).astype(np.float32)


def _load_cache(cache_dir: Path | None) -> dict[str, np.ndarray]:
    if not cache_dir or not (cache_dir / "chunks.jsonl").exists():
        return {}
    keys = [json.loads(line)["embedding_key"] for line in _lines(cache_dir / "chunks.jsonl")]
    vecs = np.load(cache_dir / "embeddings.npy")
    return dict(zip(keys, vecs, strict=True))


def _lines(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@dataclass(frozen=True)
class BuildReport:
    out_dir: Path
    n_docs: int
    n_chunks: int
    n_embedded: int  # new embeddings requested from the API (the rest came from the cache)
    missing: list[str]
    content_sha256: str


def build_index(
    raw_dir: Path,
    out_dir: Path,
    *,
    cache_dir: Path | None = None,
    sources_path: Path | None = None,
    embed_fn: EmbedFn | None = None,
    settings: Settings | None = None,
) -> BuildReport:
    from milewise.rag.embeddings import embed

    s = settings or get_settings()
    model, dims = s.embedding_model, s.embedding_dimensions
    cfg = yaml.safe_load((sources_path or data_dir() / "sources.yaml").read_text(encoding="utf-8"))
    records: list[dict[str, Any]] = []
    docs: list[dict[str, Any]] = []
    missing: list[str] = []
    for src in cfg["sources"]:
        if src["kind"] != "rules":
            continue
        path = newest_raw(raw_dir, src)
        if path is None:
            missing.append(src["id"])
            continue
        sections = extract_sections(path.read_text(encoding="utf-8"))
        chunks = chunk_sections(sections)
        m = _TS.search(path.name)
        fetched = datetime.strptime(m.group(1), "%Y%m%dT%H%M%S") if m else None
        docs.append(
            {
                "source_id": src["id"],
                "raw_file": path.name,
                "fetched_date": fetched.date().isoformat() if fetched else None,
                "n_chunks": len(chunks),
            }
        )
        title = sections[0].path[0] if sections else src["id"]
        for c in chunks:
            records.append(
                {
                    "chunk_id": c.chunk_id(src["id"]),
                    "source_id": src["id"],
                    "airline": src["airline"],
                    "url": src["url"],
                    "title": title,
                    "doc_type": src.get("doc_type", "rules"),
                    "award": award_of(src),
                    "lang": src.get("lang", "ja"),
                    "fetched_date": docs[-1]["fetched_date"],
                    "ord": c.ord,
                    "section_path": list(c.section_path),
                    "text": c.text,
                    "body_sha256": _sha(_body(c)),
                    "embedding_key": embedding_key(model, dims, c.text),
                }
            )

    cached = _load_cache(cache_dir)
    todo = list(
        dict.fromkeys(r["embedding_key"] for r in records if r["embedding_key"] not in cached)
    )
    texts = {r["embedding_key"]: r["text"] for r in records}
    if todo:
        vectors = (embed_fn or (lambda t: embed(t, s)))([texts[k] for k in todo])
        cached.update(zip(todo, np.asarray(vectors, dtype=np.float32), strict=True))
    matrix = (
        _normalise(np.stack([cached[r["embedding_key"]] for r in records]))
        if records
        else np.zeros((0, dims), dtype=np.float32)
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records)
    (out_dir / "chunks.jsonl").write_text(body, encoding="utf-8")
    np.save(out_dir / "embeddings.npy", matrix)
    content_sha = _sha(body)
    manifest = {
        "schema": INDEX_SCHEMA,
        "content_sha256": content_sha,
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "chunker_version": CHUNKER_VERSION,
        "embedding_model": model,
        "embedding_dimensions": dims,
        "n_docs": len(docs),
        "n_chunks": len(records),
        "docs": docs,
        "missing": missing,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return BuildReport(out_dir, len(docs), len(records), len(todo), missing, content_sha)


class RulesIndex:
    def __init__(self, chunks: list[dict[str, Any]], vectors: np.ndarray, manifest: dict[str, Any]):
        assert len(chunks) == vectors.shape[0], "chunks.jsonl and embeddings.npy disagree"
        self.chunks, self.vectors, self.manifest = chunks, vectors, manifest
        self._airline = np.array([c["airline"] for c in chunks])
        self._award = np.array([c.get("award", "general") for c in chunks])
        self._url = np.array([c["url"] for c in chunks])

    @classmethod
    def load(cls, index_dir: Path | None = None) -> RulesIndex:
        d = index_dir or default_index_dir()
        if not (d / "manifest.json").exists():
            raise FileNotFoundError(f"no rules index in {d}: run `make index`")
        manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("schema") != INDEX_SCHEMA:
            raise ValueError(f"index schema {manifest.get('schema')} != {INDEX_SCHEMA}: rebuild it")
        chunks = [json.loads(line) for line in _lines(d / "chunks.jsonl")]
        return cls(chunks, np.load(d / "embeddings.npy"), manifest)

    @property
    def version(self) -> dict[str, Any]:
        m = self.manifest
        return {k: m.get(k) for k in ("content_sha256", "built_at", "n_chunks", "embedding_model")}

    def recall(
        self, vec: Sequence[float], airline: str | None, k: int, award: str | None = None
    ) -> list[dict[str, Any]]:
        """Nearest chunks by cosine similarity, one per distinct body text.

        The same notice is printed on many pages; without the de-duplication its copies
        crowd out other evidence (design.md §9.10). Ties break on URL so results are stable.
        """
        if not self.chunks:
            return []
        q = np.asarray(vec, dtype=np.float32)
        q = q / (np.linalg.norm(q) or 1.0)
        scores = self.vectors @ q
        mask = np.ones(len(self.chunks), bool) if not airline else self._airline == airline
        if award in ("own", "partner"):  # the other award type's pages are left out
            mask &= self._award != ("partner" if award == "own" else "own")
        idx = np.flatnonzero(mask)
        order = idx[np.lexsort((self._url[idx], -scores[idx]))]
        out, seen = [], set()
        for i in order:
            c = self.chunks[i]
            if c["body_sha256"] in seen:
                continue
            seen.add(c["body_sha256"])
            out.append(
                {
                    "chunk_id": c["chunk_id"],
                    "text": c["text"],
                    "title": c["title"],
                    "source_url": c["url"],
                    "airline": c["airline"],
                    "doc_type": c["doc_type"],
                    "award": c.get("award", "general"),
                    "section": " > ".join(c.get("section_path", [])[1:]),
                    "fetched_date": c["fetched_date"],
                    "score": float(scores[i]),
                }
            )
            if len(out) == k:
                break
        return out


@cache
def get_rules_index() -> RulesIndex:
    return RulesIndex.load()
