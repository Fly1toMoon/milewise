"""Editor notes on rule sentences the official text leaves open (design.md §4.13)."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

import pytest
import yaml

from milewise.config import Settings
from milewise.rag.index import RulesIndex, build_index, default_index_dir
from milewise.rag.notes import (
    NOTES_HEADER,
    dangling,
    load_notes,
    notes_block,
    notes_for,
    strip_notes_block,
)

OW = "https://www.jal.co.jp/jp/ja/jalmile/use/partner_air/oneworld/conditions.html"
NOTE = {
    "id": "n1",
    "url": OW,
    "section": "旅程について",
    "match": "出発国へ戻り、さらに第三国へ行く旅程は組めません。",
    "note": {
        "ja": "原文は定義していない。",
        "zh": "原文没有定义。",
        "en": "The text does not say.",
    },
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
    for n in notes:
        assert set(n["note"]) == {"ja", "zh", "en"}
        assert " " not in n["note"]["ja"]  # folded YAML lines are joined without spaces


@pytest.mark.parametrize(
    "note", [{"ja": "注", "zh": "注"}, {"ja": "注", "zh": "", "en": "n"}, "注"]
)
def test_a_note_without_every_language_is_rejected(tmp_path: Path, note: object) -> None:
    p = tmp_path / "notes.yaml"
    p.write_text(yaml.safe_dump({"notes": [{**NOTE, "note": note}]}), "utf-8")
    with pytest.raises(ValueError, match="note needs a text in"):
        load_notes(p)


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
    monkeypatch.setattr(search, "notes_for", lambda u, s, t, **k: notes_for(u, s, t, (NOTE,)))
    res = search.search_rules(Index(), "q", settings=object())  # type: ignore[arg-type]
    assert res.chunks[0]["editor_note"] == NOTE["note"]


def test_evidence_card_shows_the_note_apart_from_the_text() -> None:
    from milewise.api import evidence_card

    card = evidence_card({"chunk_id": "c1", "url": OW, "text": "x", "editor_note": "注"})
    assert card["editor_note"] == "注" and "text" not in card
    assert evidence_card({"chunk_id": "c1", "url": OW})["editor_note"] is None


CN_NOTE = {**NOTE, "id": "n2", "when_itinerary": "cn_with_hk_mo_tw"}


def test_a_conditional_note_needs_its_itinerary_flag() -> None:
    text = "ワンワールド特典航空券 > 旅程について\n" + NOTE["match"]
    assert notes_for(OW, "旅程について", text, (CN_NOTE,)) == []
    assert notes_for(OW, "旅程について", text, (CN_NOTE,), {"cn_with_hk_mo_tw"}) == [CN_NOTE]
    # an unconditional note does not depend on the flags
    assert notes_for(OW, "旅程について", text, (NOTE,), {"cn_with_hk_mo_tw"}) == [NOTE]


def test_unknown_condition_is_rejected(tmp_path: Path) -> None:
    p = tmp_path / "notes.yaml"
    p.write_text(yaml.safe_dump({"notes": [{**NOTE, "when_itinerary": "nope"}]}), "utf-8")
    with pytest.raises(ValueError, match="unknown when_itinerary"):
        load_notes(p)


def test_committed_origin_country_note_is_conditional() -> None:
    by_id = {n["id"]: n for n in load_notes()}
    assert by_id["ow-origin-country-cn"]["when_itinerary"] == "cn_with_hk_mo_tw"
    assert "when_itinerary" not in by_id["ow-japan-hatsuchaku"]


HK_SHA = [["香港", "東京", "ロンドン"], ["パリ", "上海", "東京"]]
NO_CN = [["香港", "東京", "ロンドン"], ["パリ", "東京"]]


def _flags_seen(
    monkeypatch: pytest.MonkeyPatch,
    fares: object,
    search_args: dict[str, object],
    history: list[dict[str, object]] | None = None,
) -> tuple[list[frozenset[str]], object]:
    """The itinerary flags search_rules is given in one ask() turn."""
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    from milewise import agent, ground, verify
    from milewise.rag.search import RulesResult

    class Model(GenericFakeChatModel):
        def bind_tools(self, tools: object, **kw: object) -> Model:
            return self

    seen: list[frozenset[str]] = []

    def fake_search(*a: object, **k: object) -> RulesResult:
        seen.append(frozenset(k.get("itinerary_flags") or ()))  # type: ignore[arg-type]
        chunk = {"chunk_id": "c1", "url": OW, "section": "旅程について", "text": NOTE["match"]}
        return RulesResult("ok", [chunk])  # some evidence, so ask() does not retry

    model = Model(
        messages=iter(
            [
                AIMessage(
                    "",
                    tool_calls=[
                        {
                            "name": "search_rules",
                            "args": {"question": "q", **search_args},
                            "id": "1",
                        }
                    ],
                ),
                AIMessage("答"),
            ]
        )
    )
    monkeypatch.setattr(agent.llm, "chat_model", lambda *a, **k: model)
    monkeypatch.setattr(ground, "search_rules", fake_search)
    # a follow-up that searched the rules is checked by a model: not in a unit test
    monkeypatch.setattr(verify, "model_issues", lambda *a, **k: ([], {}))
    res = agent.ask("可以吗？", fares=fares, index=object(), history=history)  # type: ignore[arg-type]
    return seen, res


def test_cn_with_hong_kong_in_this_turn_attaches(monkeypatch: pytest.MonkeyPatch, fares) -> None:  # type: ignore[no-untyped-def]
    seen, res = _flags_seen(monkeypatch, fares, {"itinerary": HK_SHA})
    assert seen == [frozenset({"cn_with_hk_mo_tw"})]
    assert res.itinerary == HK_SHA  # type: ignore[attr-defined]


def test_no_cn_with_hong_kong_does_not_attach(monkeypatch: pytest.MonkeyPatch, fares) -> None:  # type: ignore[no-untyped-def]
    assert _flags_seen(monkeypatch, fares, {"itinerary": NO_CN})[0] == [frozenset()]
    assert _flags_seen(monkeypatch, fares, {})[0] == [frozenset()]


def test_an_itinerary_from_an_earlier_answer_attaches(
    monkeypatch: pytest.MonkeyPatch, fares
) -> None:  # type: ignore[no-untyped-def]
    history = [
        {"role": "user", "content": "香港→东京→伦敦，巴黎→上海，可以吗？"},
        {
            "role": "assistant",
            "content": "可以。",
            "itinerary": [["香港", "東京", "ロンドン"], ["パリ", "上海"]],
        },
    ]
    seen, res = _flags_seen(monkeypatch, fares, {}, history)
    assert seen == [frozenset({"cn_with_hk_mo_tw"})]
    # carried on, so the next follow-up still has it
    assert res.itinerary == history[1]["itinerary"]  # type: ignore[attr-defined]


# ---------- notes shown by the system ----------

EV = {
    "D1": {"text": "a", "editor_note": "原文没有定义。"},
    "D2": {"text": "b"},
    # two notes on one excerpt, one of them shared with D1
    "D3": {"text": "c", "editor_note": "原文没有定义。\n另一条注释。"},
}


def test_a_cited_excerpt_s_note_is_appended_once() -> None:
    block = notes_block("不行 [D1]，另见 [D3] 和 [D1]。", EV, "zh")
    assert block == (
        "\n\n" + NOTES_HEADER["zh"] + "\n- [D1][D3] 原文没有定义。\n- [D3] 另一条注释。"
    )


def test_no_note_without_a_cited_noted_excerpt() -> None:
    assert notes_block("可以 [D2]，见 [C1]。", EV, "zh") == ""
    assert notes_block("没有引用。", EV, "zh") == ""


def test_the_notes_block_is_taken_out_of_history() -> None:
    from milewise.agent import prior_turns
    from milewise.verify import POSTCHECK_WARNING

    answer = "不行 [D1]。" + notes_block("不行 [D1]。", EV, "zh") + POSTCHECK_WARNING["zh"]
    assert strip_notes_block("不行。" + notes_block("[D1]", EV, "zh")) == "不行。"
    [_, a] = prior_turns(
        [{"role": "user", "content": "q"}, {"role": "assistant", "content": answer}]
    )
    assert a["content"] == "不行。"


def _ask_noted(
    monkeypatch: pytest.MonkeyPatch, fares: object, question: str, answer: str
) -> object:
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    from milewise import agent, ground
    from milewise.rag.search import RulesResult

    class Model(GenericFakeChatModel):
        def bind_tools(self, tools: object, **kw: object) -> Model:
            return self

    chunk = {"chunk_id": "c1", "url": OW, "section": "旅程について", "text": NOTE["match"]}
    chunk["editor_note"] = NOTE["note"]
    search = {"name": "search_rules", "args": {"question": "q"}, "id": "1"}
    model = Model(messages=iter([AIMessage("", tool_calls=[search]), AIMessage(answer)]))
    monkeypatch.setattr(agent.llm, "chat_model", lambda *a, **k: model)
    monkeypatch.setattr(ground, "search_rules", lambda *a, **k: RulesResult("ok", [chunk]))
    return agent.ask(question, fares=fares, index=object())  # type: ignore[arg-type]


def test_ask_appends_the_note_in_the_answer_language(
    monkeypatch: pytest.MonkeyPatch, fares
) -> None:  # type: ignore[no-untyped-def]
    res = _ask_noted(monkeypatch, fares, "Can I return to the origin country?", "No [D1].")
    assert res.answer == (  # type: ignore[attr-defined]
        "No [D1].\n\n" + NOTES_HEADER["en"] + "\n- [D1] The text does not say."
    )
    # the evidence (and the page's source card) carries the same language
    assert res.evidence["D1"]["editor_note"] == "The text does not say."  # type: ignore[attr-defined]
    assert res.postcheck_ok and res.check["revised"] is False  # type: ignore[attr-defined]


def test_ask_appends_no_note_when_the_excerpt_is_not_cited(
    monkeypatch: pytest.MonkeyPatch, fares
) -> None:  # type: ignore[no-untyped-def]
    res = _ask_noted(monkeypatch, fares, "出発国に戻れますか？", "公式の文言を確認してください。")
    assert res.answer == "公式の文言を確認してください。"  # type: ignore[attr-defined]
