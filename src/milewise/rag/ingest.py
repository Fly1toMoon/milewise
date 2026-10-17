"""Idempotent ingestion of one raw rules page (design.md §2.2).

The same function backs the local CLI and, from M4, the S3 → SQS → Lambda pipeline:
  * (raw_key, raw_etag, chunker version) already done → no-op (SQS may deliver twice)
  * extracted text identical to the current doc  → recorded as skipped_same_content
  * otherwise: new document becomes current, previous one is superseded, all in one transaction
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from milewise.db import Db
from milewise.rag.chunking import CHUNKER_VERSION, Chunk, chunk_sections, extract_sections
from milewise.rag.embeddings import embed, to_pgvector

EmbedFn = Callable[[Sequence[str]], list[list[float]]]


@dataclass(frozen=True)
class IngestResult:
    status: str  # succeeded | skipped_same_content | duplicate
    chunks_written: int = 0
    chunks_skipped_boilerplate: int = 0


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _body(chunk: Chunk) -> str:
    return chunk.text.split("\n", 1)[1] if "\n" in chunk.text else chunk.text


def ingest_page(
    db: Db,
    page_html: str,
    *,
    source: dict[str, Any],
    raw_key: str,
    raw_etag: str,
    fetched_at: str,
    embedding_model: str,
    embed_fn: EmbedFn = embed,
) -> IngestResult:
    # A new chunker version re-processes pages whose raw bytes did not change.
    raw_etag = f"{raw_etag}#chunker{CHUNKER_VERSION}"
    done = db.query(
        "SELECT status FROM rag.ingestion_runs WHERE raw_key = :k AND raw_etag = :e",
        {"k": raw_key, "e": raw_etag},
    )
    if done and done[0]["status"] in ("succeeded", "skipped_same_content"):
        return IngestResult("duplicate")
    db.execute(
        "INSERT INTO rag.ingestion_runs (raw_key, raw_etag, status) VALUES (:k, :e, 'started')"
        " ON CONFLICT (raw_key, raw_etag) DO UPDATE SET status = 'started', error = NULL,"
        " started_at = now(), finished_at = NULL",
        {"k": raw_key, "e": raw_etag},
    )
    try:
        result = _ingest(db, page_html, source, raw_key, fetched_at, embedding_model, embed_fn)
    except Exception as e:
        db.execute(
            "UPDATE rag.ingestion_runs SET status = 'failed', error = :err, finished_at = now()"
            " WHERE raw_key = :k AND raw_etag = :e",
            {"k": raw_key, "e": raw_etag, "err": f"{type(e).__name__}: {e}"[:2000]},
        )
        raise
    db.execute(
        "UPDATE rag.ingestion_runs SET status = :s, chunks_written = :n, finished_at = now()"
        " WHERE raw_key = :k AND raw_etag = :e",
        {"k": raw_key, "e": raw_etag, "s": result.status, "n": result.chunks_written},
    )
    return result


def _ingest(
    db: Db,
    page_html: str,
    source: dict[str, Any],
    raw_key: str,
    fetched_at: str,
    embedding_model: str,
    embed_fn: EmbedFn,
) -> IngestResult:
    sections = extract_sections(page_html)
    chunks = chunk_sections(sections)
    content_sha = _sha("\n\n".join(c.text for c in chunks))
    current = db.query(
        "SELECT doc_id, content_sha256 FROM rag.documents WHERE source_url = :u AND is_current",
        {"u": source["url"]},
    )
    if current and current[0]["content_sha256"] == content_sha:
        return IngestResult("skipped_same_content")

    # Boilerplate (identical notices on every page) is stored once per airline.
    seen = {
        r["body_sha256"]
        for r in db.query(
            "SELECT c.body_sha256 FROM rag.chunks c JOIN rag.documents d USING (doc_id)"
            " WHERE d.is_current AND d.airline = :a AND d.source_url <> :u",
            {"a": source["airline"], "u": source["url"]},
        )
    }
    keep = [c for c in chunks if _sha(_body(c)) not in seen]
    vectors = embed_fn([c.text for c in keep]) if keep else []

    with db.transaction():
        if current:
            db.execute(
                "UPDATE rag.documents SET is_current = false, superseded_at = now()"
                " WHERE doc_id = CAST(:d AS uuid)",
                {"d": current[0]["doc_id"]},
            )
        doc = db.query(
            "INSERT INTO rag.documents (source_id, airline, source_url, lang, title, doc_type,"
            " content_sha256, raw_key, fetched_at) VALUES (:sid, :a, :u, :lang, :title, :dt,"
            " :sha, :raw, CAST(:fetched AS timestamptz)) RETURNING doc_id",
            {
                "sid": source["id"],
                "a": source["airline"],
                "u": source["url"],
                "lang": source.get("lang", "ja"),
                "title": sections[0].path[0] if sections else source["id"],
                "dt": source.get("doc_type", "rules"),
                "sha": content_sha,
                "raw": raw_key,
                "fetched": fetched_at,
            },
        )[0]["doc_id"]
        db.execute_many(
            "INSERT INTO rag.chunks (chunk_id, doc_id, ord, section_path, text, body_sha256,"
            " char_count, embedding, embedding_model, lang) VALUES (:id, CAST(:doc AS uuid), :ord,"
            " string_to_array(:path, ' > '), :text, :body, :n, CAST(:vec AS vector), :model, :lang)"
            " ON CONFLICT (chunk_id) DO NOTHING",
            [
                {
                    "id": c.chunk_id(str(doc)),
                    "doc": str(doc),
                    "ord": c.ord,
                    "path": " > ".join(c.section_path),
                    "text": c.text,
                    "body": _sha(_body(c)),
                    "n": len(c.text),
                    "vec": to_pgvector(v),
                    "model": embedding_model,
                    "lang": source.get("lang", "ja"),
                }
                for c, v in zip(keep, vectors, strict=True)
            ],
        )
    return IngestResult("succeeded", len(keep), len(chunks) - len(keep))
