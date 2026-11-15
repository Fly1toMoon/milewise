"""Ground and verify: forced lookups, the retry, and one rewrite with every issue found
(design.md §4.13)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from milewise import agent, ground, verify
from milewise.evidence import Evidence
from milewise.rag.search import RulesResult
from milewise.verify import Issue, needs_model_check, revise_note

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
DRAFT = "你说得对，日本发着的规则适用 [D1]。"
REWRITE = "原文只写了日本発着 [D1]，没有定义只到达日本是否算，需要向 JAL 确认。"
BAD_MARK = "原文只写了日本発着 [D7]。"  # D7 does not exist in this turn
ISSUE = Issue("model", "unjustified_concession", DRAFT, "agrees without support")


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
    verdicts: list[list[Issue]],
    history: list[dict[str, Any]] | None = HISTORY,
    on_event: Any = None,
) -> tuple[Any, list[str], list[Any]]:
    """ask() with a scripted model and model check; returns the result, the answers the
    model check saw, and the messages the model was given on each call."""
    seen: list[str] = []
    calls: list[Any] = []
    model = Model(messages=iter(replies))
    original = Model._generate

    def spy(self: Model, messages: list[Any], *a: Any, **k: Any) -> Any:
        calls.append(list(messages))
        return original(self, messages, *a, **k)

    monkeypatch.setattr(Model, "_generate", spy)
    monkeypatch.setattr(agent.llm, "chat_model", lambda *a, **k: model)
    monkeypatch.setattr(ground, "search_rules", lambda *a, **k: RulesResult("ok", [CHUNK]))
    monkeypatch.setattr(agent, "_excerpts", lambda history, index: None)

    def fake_check(answer: str, *a: Any, **k: Any) -> tuple[list[Issue], dict[str, int]]:
        seen.append(answer)
        return verdicts.pop(0), {"input_tokens": 1000, "output_tokens": 100}

    monkeypatch.setattr(verify, "model_issues", fake_check)
    res = agent.ask(
        "不是吧，违反的应该是日本发着不能在日本停留的规则吧",
        fares=fares,  # type: ignore[arg-type]
        index=object(),  # type: ignore[arg-type]
        history=history,
        on_event=on_event,
    )
    return res, seen, calls


def test_only_follow_ups_that_searched_the_rules_are_model_checked() -> None:
    assert needs_model_check(HISTORY, ["search_rules"])
    assert not needs_model_check(HISTORY, ["get_required_miles"])
    assert not needs_model_check([], ["search_rules"])
    assert not needs_model_check([{"role": "user", "content": "q"}], ["search_rules"])


def test_a_first_question_is_not_model_checked(monkeypatch: pytest.MonkeyPatch, fares) -> None:  # type: ignore[no-untyped-def]
    res, seen, calls = _ask(monkeypatch, fares, [SEARCH, AIMessage(DRAFT)], [], history=None)
    assert seen == [] and res.answer == DRAFT and len(calls) == 2
    assert res.check["model_checked"] is False and res.check["revised"] is False
    assert res.postcheck_ok


def test_a_passing_answer_stands(monkeypatch: pytest.MonkeyPatch, fares) -> None:  # type: ignore[no-untyped-def]
    res, seen, calls = _ask(monkeypatch, fares, [SEARCH, AIMessage(REWRITE)], [[]])
    assert seen == [REWRITE] and res.answer == REWRITE
    assert res.check["model_checked"] is True and res.check["revised"] is False
    assert res.check["issues"] == [] and len(calls) == 2  # no rewrite


def test_a_model_issue_is_rewritten_once(monkeypatch: pytest.MonkeyPatch, fares) -> None:  # type: ignore[no-untyped-def]
    events: list[tuple[str, dict[str, Any]]] = []
    res, seen, calls = _ask(
        monkeypatch,
        fares,
        [SEARCH, AIMessage(DRAFT), AIMessage(REWRITE)],
        [[ISSUE]],
        on_event=lambda k, d: events.append((k, d)),
    )
    assert seen == [DRAFT]
    assert res.answer == REWRITE  # only the rewrite, not the draft
    assert res.check["revised"] is True
    assert res.check["issues"] == [
        {
            "source": "model",
            "kind": "unjustified_concession",
            "quote": DRAFT,
            "problem": ISSUE.problem,
        }
    ]
    assert res.check["cost_usd"] > 0
    # the rewrite sees the tool results, the draft and the review
    last = calls[-1]
    assert any(isinstance(m, ToolMessage) for m in last)
    assert isinstance(last[-1], HumanMessage) and "model: unjustified_concession" in last[-1].text
    # the page is told to drop the streamed draft before the rewrite streams
    kinds = [k for k, _ in events]
    assert kinds.index("revise") < len(kinds) - 1 and ("status", {"stage": "checking"}) in events


def test_a_code_issue_is_rewritten_once(monkeypatch: pytest.MonkeyPatch, fares) -> None:  # type: ignore[no-untyped-def]
    events: list[tuple[str, dict[str, Any]]] = []
    res, seen, calls = _ask(
        monkeypatch,
        fares,
        [SEARCH, AIMessage(BAD_MARK), AIMessage(REWRITE)],
        [],
        history=None,  # no model check: the code check alone asks for the rewrite
        on_event=lambda k, d: events.append((k, d)),
    )
    assert seen == [] and res.answer == REWRITE and res.postcheck_ok
    assert [i["source"] for i in res.check["issues"]] == ["code"]
    assert res.check["issues"][0]["quote"] == "[D7]" and res.check["revised"] is True
    note = calls[-1][-1]
    assert isinstance(note, HumanMessage) and '(code: citation) "[D7]"' in note.text
    assert "revise" in [k for k, _ in events]
    assert ("status", {"stage": "checking"}) not in events  # no model check ran


def test_code_and_model_issues_make_one_rewrite(monkeypatch: pytest.MonkeyPatch, fares) -> None:  # type: ignore[no-untyped-def]
    events: list[tuple[str, dict[str, Any]]] = []
    res, _, calls = _ask(
        monkeypatch,
        fares,
        [SEARCH, AIMessage(BAD_MARK), AIMessage(REWRITE)],
        [[ISSUE]],
        on_event=lambda k, d: events.append((k, d)),
    )
    assert len(calls) == 3 and [k for k, _ in events].count("revise") == 1
    assert [i["source"] for i in res.check["issues"]] == ["code", "model"]
    note = calls[-1][-1].text
    assert "code: citation" in note and "model: unjustified_concession" in note
    assert res.answer == REWRITE


def test_the_rewrite_gets_only_the_code_check(monkeypatch: pytest.MonkeyPatch, fares) -> None:  # type: ignore[no-untyped-def]
    res, seen, calls = _ask(
        monkeypatch,
        fares,
        [SEARCH, AIMessage(DRAFT), AIMessage("又一次附和 [D1]。")],
        [[ISSUE], [ISSUE]],
    )
    assert seen == [DRAFT]  # the model check does not run on the rewrite
    assert res.answer == "又一次附和 [D1]。" and len(calls) == 3 and res.postcheck_ok


def test_a_rewrite_that_still_fails_the_code_check_is_flagged(
    monkeypatch: pytest.MonkeyPatch, fares
) -> None:  # type: ignore[no-untyped-def]
    res, _, calls = _ask(
        monkeypatch,
        fares,
        [SEARCH, AIMessage(DRAFT), AIMessage("需要 99,999 里程 [D7]。")],
        [[ISSUE]],
    )
    assert len(calls) == 3  # still one rewrite
    assert not res.postcheck_ok
    assert [i["quote"] for i in res.check["remaining"]] == ["99,999 里程", "[D7]"]
    assert res.answer.endswith(verify.POSTCHECK_WARNING["zh"])


def test_revise_note_lists_each_issue() -> None:
    note = revise_note([ISSUE, Issue("code", "url", "https://x.example/", "not in results")])
    assert DRAFT in note and "(model: unjustified_concession)" in note
    assert '(code: url) "https://x.example/"' in note


# ---------- grounding ----------


class Request:
    def __init__(self, messages: list[Any], tool_choice: Any = None) -> None:
        self.messages = messages
        self.tool_choice = tool_choice

    def override(self, **k: Any) -> Request:
        return Request(self.messages, k["tool_choice"])


def test_force_tool_applies_until_the_first_tool_result() -> None:
    force = ground.ForceTool("search_rules")
    got: list[Any] = []
    first = [HumanMessage("上文"), AIMessage("答"), HumanMessage("不是吧")]
    force.wrap_model_call(Request(first), lambda r: got.append(r.tool_choice))  # type: ignore[arg-type]
    after = [*first, SEARCH, ToolMessage("{}", tool_call_id="1")]
    force.wrap_model_call(Request(after), lambda r: got.append(r.tool_choice))  # type: ignore[arg-type]
    assert got == ["search_rules", None]


def test_a_follow_up_to_a_rule_answer_must_search_first() -> None:
    assert ground.first_lookup(HISTORY) == "search_rules"
    assert ground.first_lookup([{"role": "assistant", "content": "110,000 里程"}]) is None
    assert ground.first_lookup(None) is None


class FakeRun:
    """Records each run's arguments; `cites` says whether a run adds evidence."""

    def __init__(self, evidence: Evidence, cites: list[bool]) -> None:
        self.evidence, self.cites = evidence, cites
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, *, note: str = "", force: str | None = None, hold: bool = False
    ) -> list[Any]:
        self.calls.append({"note": note, "force": force, "hold": hold})
        if self.cites.pop(0):
            self.evidence.add_doc(CHUNK)
        return [AIMessage(f"run {len(self.calls)}")]


def test_the_draft_of_a_rule_follow_up_is_forced_to_search() -> None:
    ev = Evidence()
    run = FakeRun(ev, [True])
    ground.grounded_draft(run, HISTORY, ev)
    assert run.calls == [{"note": "", "force": "search_rules", "hold": True}]
    run = FakeRun(Evidence(), [True])
    ground.grounded_draft(run, None, run.evidence)
    assert run.calls[0]["force"] is None


def test_a_draft_without_evidence_is_retried_once() -> None:
    ev = Evidence()
    run = FakeRun(ev, [False, False])
    messages = ground.grounded_draft(run, None, ev)
    assert [c["note"] for c in run.calls] == ["", ground.RETRY_NOTE]
    assert run.calls[0]["hold"] and not run.calls[1]["hold"]  # the first draft is not shown
    assert messages[0].text == "run 2"


def test_model_check_sees_the_conversation_and_decides_in_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from milewise.config import Settings

    prompts: list[str] = []
    raw = {"sentence": DRAFT, "kind": "unjustified_concession", "problem": "agrees"}

    def structured(*a: Any, **k: Any) -> Any:
        prompts.append(k["prompt"])
        assert k["temperature"] == 0
        return {"issues": [raw, {"sentence": "x", "kind": "style", "problem": "p"}]}, {}

    monkeypatch.setattr(verify.llm, "structured", structured)
    history = [
        {"role": "user", "content": "第一问：马尼拉出发的票"},
        {"role": "assistant", "content": "第一答"},
        {"role": "user", "content": "香港出发的行程呢"},
        {"role": "assistant", "content": "上一答"},
    ]
    ev = {"D1": {**CHUNK, "editor_note": "Milewise 注"}, "C1": {"itinerary_facts": {"n": 4}}}
    issues, _ = verify.model_issues(DRAFT, "不是吧", history, ev, Settings())
    # an unknown kind is dropped
    assert issues == [Issue("model", "unjustified_concession", DRAFT, "agrees")]
    p = prompts[0]
    for part in ["第一问", "香港出发的行程呢", "上一答", "不是吧", "Milewise 注", '"n": 4', DRAFT]:
        assert part in p
    assert "第一答" not in p


def test_model_check_knows_a_definite_answer_on_an_undefined_point_is_unsupported() -> None:
    # multi-07: the answer said "可以" where the editor_note says the text does not define it
    text = verify.CHECK_INSTRUCTIONS
    assert "editor_note says the" in text and "stated as definite" in text
    assert verify.MODEL_KINDS == (
        "unsupported",
        "conflicts_with_ticketed",
        "unjustified_concession",
    )
