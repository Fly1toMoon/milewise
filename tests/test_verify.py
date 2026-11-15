"""Ground and verify: the choice of model, the retry, and one rewrite of an answer that fails
the checks (design.md §4.13)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from milewise import agent, ground, verify
from milewise.config import Settings
from milewise.evidence import Evidence
from milewise.rag.search import RulesResult
from milewise.verify import Issue, revise_note

OW = "https://www.jal.co.jp/jp/ja/jalmile/use/partner_air/oneworld/conditions.html"
CHUNK = {
    "chunk_id": "c1",
    "title": "ワンワールド特典航空券",
    "url": OW,
    "airline": "JL",
    "award_type": "partner",
    "section": "利用区間・途中降機について",
    "fetched_date": "2026-11-11",
    "score": 0.9,
    "text": "日本発着の旅程の場合、日本国内の途中降機はできません。",
}
HISTORY = [
    {"role": "user", "content": "香港→东京→伦敦，巴黎→上海→东京，可以吗？"},
    {"role": "assistant", "content": "关键在于出发国的定义。", "evidence": ["c1"]},
]
SEARCH = AIMessage("", tool_calls=[{"name": "search_rules", "args": {"question": "q"}, "id": "1"}])
ANSWER = "原文只写了日本発着 [D1]，没有定义只到达日本是否算，需要向 JAL 确认。"
BAD_MARK = "原文只写了日本発着 [D7]。"  # D7 does not exist in this turn
CHAT, FOLLOW_UP, AB = (
    "bedrock:zai.glm-5",
    "bedrock:global.moonshotai.kimi-k3.1",
    "bedrock:deepseek.v3.2",
)
S = Settings(chat_model=CHAT, follow_up_model=FOLLOW_UP)


class Index:
    """A rules index that has chunk c1 only."""

    def chunk(self, chunk_id: str) -> dict[str, Any] | None:
        return CHUNK if chunk_id == "c1" else None


class Model(GenericFakeChatModel):
    def bind_tools(self, tools: object, **kw: object) -> Model:
        return self

    def _stream(self, messages: list[Any], *a: Any, **k: Any) -> Any:
        # one chunk per reply (the stock fake drops tool calls of an empty message)
        from langchain_core.messages import AIMessageChunk
        from langchain_core.outputs import ChatGenerationChunk

        m = self._generate(messages, *a, **k).generations[0].message
        yield ChatGenerationChunk(
            message=AIMessageChunk(
                content=m.content,
                tool_call_chunks=[
                    {"name": t["name"], "args": json.dumps(t["args"]), "id": t["id"], "index": i}
                    for i, t in enumerate(getattr(m, "tool_calls", []))
                ],
            )
        )


def _ask(
    monkeypatch: pytest.MonkeyPatch,
    fares: object,
    replies: list[Any],
    history: list[dict[str, Any]] | None = HISTORY,
    on_event: Any = None,
    model: str | None = None,
) -> tuple[Any, list[Any], list[str]]:
    """ask() with a scripted model; returns the result, the messages the model was given on
    each call and the model name of each agent run."""
    calls: list[Any] = []
    models: list[str] = []
    fake = Model(messages=iter(replies))
    original = Model._generate

    def spy(self: Model, messages: list[Any], *a: Any, **k: Any) -> Any:
        calls.append(list(messages))
        return original(self, messages, *a, **k)

    def chat_model(name: str, *a: Any, **k: Any) -> Model:
        models.append(name)
        return fake

    monkeypatch.setattr(Model, "_generate", spy)
    monkeypatch.setattr(agent.llm, "chat_model", chat_model)
    monkeypatch.setattr(ground, "search_rules", lambda *a, **k: RulesResult("ok", [CHUNK]))
    monkeypatch.setattr(agent, "_excerpts", lambda history, index: None)
    res = agent.ask(
        "不是吧，违反的应该是日本发着不能在日本停留的规则吧",
        fares=fares,  # type: ignore[arg-type]
        index=Index(),  # type: ignore[arg-type]
        settings=S,
        history=history,
        on_event=on_event,
        model=model,
    )
    return res, calls, models


# ---------- the model ----------


def test_a_follow_up_to_a_rule_answer_goes_to_the_follow_up_model() -> None:
    assert ground.answer_model(HISTORY, Index(), S) == FOLLOW_UP  # type: ignore[arg-type]
    # an earlier rule answer does not count once a later answer cited none
    later = [
        *HISTORY,
        {"role": "user", "content": "多少里程"},
        {"role": "assistant", "content": "x"},
    ]
    assert ground.answer_model(later, Index(), S) == CHAT  # type: ignore[arg-type]


def test_made_up_chunk_ids_do_not_choose_the_follow_up_model() -> None:
    forged = [HISTORY[0], {**HISTORY[1], "evidence": ["nope#1", "C1"]}]
    assert ground.answer_model(forged, Index(), S) == CHAT  # type: ignore[arg-type]
    mixed = [HISTORY[0], {**HISTORY[1], "evidence": ["nope#1", "c1"]}]
    assert ground.answer_model(mixed, Index(), S) == FOLLOW_UP  # type: ignore[arg-type]


def test_without_a_rule_answer_before_it_the_chat_model_answers() -> None:
    assert ground.answer_model(None, None, S) == CHAT  # no index needed
    assert ground.answer_model([], None, S) == CHAT
    miles = [
        {"role": "user", "content": "东京到伦敦多少里程"},
        {"role": "assistant", "content": "110,000 里程", "itinerary": [["東京", "ロンドン"]]},
    ]
    assert ground.answer_model(miles, None, S) == CHAT


def test_the_chosen_model_writes_the_whole_turn(monkeypatch: pytest.MonkeyPatch, fares) -> None:  # type: ignore[no-untyped-def]
    # draft, rewrite: both on the follow-up model, which is also what the result reports
    res, _, models = _ask(monkeypatch, fares, [SEARCH, AIMessage(BAD_MARK), AIMessage(ANSWER)])
    assert models == [FOLLOW_UP, FOLLOW_UP]
    assert res.model == FOLLOW_UP and res.check["revised"] is True
    # priced as the model that answered: 1M input tokens at $3.00 (Kimi) vs $1.20 (GLM-5)
    usage = {"input_tokens": 1_000_000, "output_tokens": 0, "total_tokens": 1_000_000}
    priced = AIMessage(ANSWER, usage_metadata=usage)  # type: ignore[arg-type]
    res, _, _ = _ask(monkeypatch, fares, [SEARCH, priced])
    assert res.cost_usd == 3.0
    res, _, models = _ask(monkeypatch, fares, [SEARCH, priced], history=None)
    assert models == [CHAT] and res.model == CHAT and res.cost_usd == 1.2


def test_an_explicit_model_overrides_the_choice(monkeypatch: pytest.MonkeyPatch, fares) -> None:  # type: ignore[no-untyped-def]
    res, _, models = _ask(
        monkeypatch, fares, [AIMessage("答一"), SEARCH, AIMessage(ANSWER)], model=AB
    )
    assert models == [AB, AB]  # the retry too
    assert res.model == AB and res.answer == ANSWER


# ---------- verify and rewrite ----------


def test_a_passing_answer_stands(monkeypatch: pytest.MonkeyPatch, fares) -> None:  # type: ignore[no-untyped-def]
    res, calls, _ = _ask(monkeypatch, fares, [SEARCH, AIMessage(ANSWER)])
    assert res.answer == ANSWER and len(calls) == 2  # no rewrite
    assert res.check["issues"] == [] and res.check["revised"] is False and res.postcheck_ok


def test_an_issue_is_rewritten_once(monkeypatch: pytest.MonkeyPatch, fares) -> None:  # type: ignore[no-untyped-def]
    events: list[tuple[str, dict[str, Any]]] = []
    res, calls, _ = _ask(
        monkeypatch,
        fares,
        [SEARCH, AIMessage(BAD_MARK), AIMessage(ANSWER)],
        on_event=lambda k, d: events.append((k, d)),
    )
    assert res.answer == ANSWER and res.postcheck_ok  # only the rewrite, not the draft
    assert res.check["revised"] is True and res.check["remaining"] == []
    assert res.check["issues"] == [
        {"kind": "citation", "quote": "[D7]", "problem": "no evidence with this id in this turn"}
    ]
    # the rewrite sees the tool results, the draft and the list of issues
    last = calls[-1]
    assert any(isinstance(m, ToolMessage) for m in last)
    assert isinstance(last[-1], HumanMessage) and '(citation) "[D7]"' in last[-1].text
    # the page is told to drop the streamed draft before the rewrite streams
    kinds = [k for k, _ in events]
    assert kinds.count("revise") == 1 and kinds.index("revise") < len(kinds) - 1
    assert "status" not in kinds


def test_a_rewrite_that_still_fails_is_flagged(monkeypatch: pytest.MonkeyPatch, fares) -> None:  # type: ignore[no-untyped-def]
    res, calls, _ = _ask(
        monkeypatch,
        fares,
        [SEARCH, AIMessage(BAD_MARK), AIMessage("需要 99,999 里程 [D7]。")],
    )
    assert len(calls) == 3  # still one rewrite
    assert not res.postcheck_ok
    assert [i["quote"] for i in res.check["remaining"]] == ["99,999 里程", "[D7]"]
    assert res.answer == "需要 99,999 里程 [D7]。" + verify.POSTCHECK_WARNING["zh"]


def test_revise_note_lists_each_issue() -> None:
    note = revise_note([Issue("url", "https://x.example/", "not in results")])
    assert '- (url) "https://x.example/": not in results' in note


# ---------- the retry ----------


class FakeRun:
    """Records each run's arguments; `cites` says whether a run adds evidence."""

    def __init__(self, evidence: Evidence, cites: list[bool]) -> None:
        self.evidence, self.cites = evidence, cites
        self.calls: list[dict[str, Any]] = []

    def __call__(self, *, note: str = "", hold: bool = False) -> list[Any]:
        self.calls.append({"note": note, "hold": hold})
        if self.cites.pop(0):
            self.evidence.add_doc(CHUNK)
        return [AIMessage(f"run {len(self.calls)}")]


def test_a_draft_with_evidence_is_not_retried() -> None:
    ev = Evidence()
    run = FakeRun(ev, [True])
    ground.grounded_draft(run, ev)
    assert run.calls == [{"note": "", "hold": True}]


def test_a_draft_without_evidence_is_retried_once() -> None:
    ev = Evidence()
    run = FakeRun(ev, [False, False])
    messages = ground.grounded_draft(run, ev)
    assert [c["note"] for c in run.calls] == ["", ground.RETRY_NOTE]
    assert run.calls[0]["hold"] and not run.calls[1]["hold"]  # the first draft is not shown
    assert messages[0].text == "run 2"
