"""Editor notes on rule sentences the official text leaves open (design.md §4.13)."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

import pytest
import yaml

from milewise.config import Settings
from milewise.rag.index import RulesIndex, build_index, default_index_dir
from milewise.rag.notes import dangling, load_notes, notes_for

OW = "https://www.jal.co.jp/jp/ja/jalmile/use/partner_air/oneworld/conditions.html"
NOTE = {
    "id": "n1",
    "url": OW,
    "section": "旅程について",
    "match": "出発国へ戻り、さらに第三国へ行く旅程は組めません。",
    "note": "原文は定義していない。",
}


def _embed(texts: Sequence[str]) -> list[list[float]]:
    return [[b / 255 for b in hashlib.sha256(t.encode()).digest()] * 32 for t in texts]


def test_note_needs_page_section_and_sentence() -> None:
    text = "ワンワールド特典航空券 > 旅程について\n片道でもご利用いただけます。\n" + NOTE["match"]
    assert notes_for(OW, "旅程について", text, (NOTE,)) == [NOTE]
    assert notes_for(OW, "利用区間・途中降機について", text, (NOTE,)) == []
    assert notes_for("https://other.example/", "旅程について", text, (NOTE,)) == []
    assert notes_for(OW, "旅程について", "片道でもご利用いただけます。", (NOTE,)) == []


def test_committed_notes_are_complete() -> None:
    notes = load_notes()
    assert {n["id"] for n in notes} == {"ow-japan-hatsuchaku", "ow-origin-country-cn"}


def test_committed_notes_match_the_index() -> None:
    if not (default_index_dir() / "manifest.json").exists():
        pytest.skip("no rules index (make index)")
    assert dangling(RulesIndex.load().chunks, load_notes()) == []


def _site(tmp: Path, body: str, more: str = "") -> Path:
    (tmp / "raw" / "jl").mkdir(parents=True)
    (tmp / "raw" / "jl" / "jal-ow-conditions_20261111T100000.html").write_text(
        f"<h1>ワンワールド特典航空券</h1><h2>旅程について</h2><p>{body}</p>{more}", encoding="utf-8"
    )
    src = {
        "id": "jal-ow-conditions",
        "airline": "JL",
        "url": OW,
        "kind": "rules",
        "doc_type": "partner",
    }
    (tmp / "sources.yaml").write_text(yaml.safe_dump({"sources": [src]}), encoding="utf-8")
    return tmp / "sources.yaml"


def test_index_build_fails_when_a_note_loses_its_sentence(tmp_path: Path) -> None:
    sources = _site(tmp_path, "片道でもご利用いただけます。")  # the noted sentence is gone
    with pytest.raises(ValueError, match="ow-origin-country-cn"):
        build_index(
            tmp_path / "raw",
            tmp_path / "index",
            sources_path=sources,
            embed_fn=_embed,
            settings=Settings(embedding_model="bedrock:fake", embedding_dimensions=1024),
        )


def test_index_build_passes_with_the_sentence(tmp_path: Path) -> None:
    sources = _site(
        tmp_path,
        "片道でもご利用いただけます。出発国へ戻り、さらに第三国へ行く旅程は組めません。",
        "<h2>利用区間・途中降機について</h2><p>日本発着の旅程の場合、日本国内の途中降機はできません。</p>",
    )
    rep = build_index(
        tmp_path / "raw",
        tmp_path / "index",
        sources_path=sources,
        embed_fn=_embed,
        settings=Settings(embedding_model="bedrock:fake", embedding_dimensions=1024),
    )
    assert rep.n_chunks >= 1


def test_search_rules_attaches_the_note(monkeypatch: pytest.MonkeyPatch) -> None:
    from milewise.rag import search

    text = "ワンワールド特典航空券 > 旅程について\n" + NOTE["match"]
    row = {
        "chunk_id": "c1",
        "text": text,
        "title": "ワンワールド特典航空券",
        "source_url": OW,
        "airline": "JL",
        "award": "partner",
        "section": "旅程について",
        "fetched_date": "2026-11-11",
        "score": 0.9,
    }

    class Index:
        def page(self, url: str) -> list[dict[str, object]]:
            return []

    monkeypatch.setattr(search, "recall", lambda *a, **k: [row])
    monkeypatch.setattr(search, "rerank", lambda q, rows, s: ([0], {}))
    monkeypatch.setattr(search, "notes_for", lambda u, s, t: notes_for(u, s, t, (NOTE,)))
    res = search.search_rules(Index(), "q", settings=object())  # type: ignore[arg-type]
    assert res.chunks[0]["editor_note"] == "原文は定義していない。"


def test_evidence_card_shows_the_note_apart_from_the_text() -> None:
    from milewise.api import evidence_card

    card = evidence_card({"chunk_id": "c1", "url": OW, "text": "x", "editor_note": "注"})
    assert card["editor_note"] == "注" and "text" not in card
    assert evidence_card({"chunk_id": "c1", "url": OW})["editor_note"] is None
