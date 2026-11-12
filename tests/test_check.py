"""Post-answer check of rule follow-ups and the forced re-search (design.md §4.13, step 6)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from milewise import agent
from milewise.check import CheckResult, revise_note, should_check
from milewise.rag.search import RulesResult

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
ISSUE = {"sentence": DRAFT, "kind": "unjustified_concession", "problem": "agrees without support"}


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
    verdicts: list[CheckResult],
    history: list[dict[str, Any]] | None = HISTORY,
    on_event: Any = None,
) -> tuple[Any, list[str], list[Any]]:
    """ask() with a scripted model and check; returns the result, the answers the check
    saw, and the messages the model was given on each call."""
    seen: list[str] = []
    calls: list[Any] = []
    model = Model(messages=iter(replies))
    original = Model._generate

    def spy(self: Model, messages: list[Any], *a: Any, **k: Any) -> Any:
        calls.append(list(messages))
        return original(self, messages, *a, **k)

    monkeypatch.setattr(Model, "_generate", spy)
    monkeypatch.setattr(agent.llm, "chat_model", lambda *a, **k: model)
    monkeypatch.setattr(agent, "search_rules", lambda *a, **k: RulesResult("ok", [CHUNK]))
    monkeypatch.setattr(agent, "_excerpt_lookup", lambda index: lambda cid: None)

    def fake_check(answer: str, *a: Any, **k: Any) -> CheckResult:
        seen.append(answer)
        return verdicts.pop(0)

    monkeypatch.setattr(agent, "check_answer", fake_check)
    res = agent.ask(
        "不是吧，违反的应该是日本发着不能在日本停留的规则吧",
        fares=fares,  # type: ignore[arg-type]
        index=object(),  # type: ignore[arg-type]
        history=history,
        on_event=on_event,
    )
    return res, seen, calls


def test_only_follow_ups_that_searched_the_rules_are_checked() -> None:
    assert should_check(HISTORY, ["search_rules"])
    assert not should_check(HISTORY, ["get_required_miles"])
    assert not should_check([], ["search_rules"])
    assert not should_check([{"role": "user", "content": "q"}], ["search_rules"])


def test_a_first_question_is_not_checked(monkeypatch: pytest.MonkeyPatch, fares) -> None:  # type: ignore[no-untyped-def]
    res, seen, _ = _ask(monkeypatch, fares, [SEARCH, AIMessage(DRAFT)], [], history=None)
    assert seen == [] and res.check is None and res.answer == DRAFT


def test_a_passing_answer_stands(monkeypatch: pytest.MonkeyPatch, fares) -> None:  # type: ignore[no-untyped-def]
    res, seen, calls = _ask(
        monkeypatch, fares, [SEARCH, AIMessage(REWRITE)], [CheckResult(True, [], {})]
    )
    assert seen == [REWRITE] and res.answer == REWRITE
    assert res.check["revised"] is False and res.check["issues"] == []
    assert len(calls) == 2  # no rewrite


def test_a_failing_answer_is_rewritten_with_the_issues(
    monkeypatch: pytest.MonkeyPatch, fares
) -> None:  # type: ignore[no-untyped-def]
    events: list[tuple[str, dict[str, Any]]] = []
    res, seen, calls = _ask(
        monkeypatch,
        fares,
        [SEARCH, AIMessage(DRAFT), AIMessage(REWRITE)],
        [CheckResult(False, [ISSUE], {"input_tokens": 1000, "output_tokens": 100})],
        on_event=lambda k, d: events.append((k, d)),
    )
    assert seen == [DRAFT]
    assert res.answer == REWRITE  # only the rewrite, not the draft
    assert res.check["revised"] is True and res.check["issues"] == [ISSUE]
    assert res.check["cost_usd"] > 0
    # the rewrite sees the tool results, the draft and the review
    last = calls[-1]
    assert any(isinstance(m, ToolMessage) for m in last)
    assert isinstance(last[-1], HumanMessage) and "unjustified_concession" in last[-1].text
    # the page is told to drop the streamed draft before the rewrite streams
    kinds = [k for k, _ in events]
    assert kinds.index("revise") < len(kinds) - 1 and ("status", {"stage": "checking"}) in events


def test_at_most_one_rewrite(monkeypatch: pytest.MonkeyPatch, fares) -> None:  # type: ignore[no-untyped-def]
    res, seen, calls = _ask(
        monkeypatch,
        fares,
        [SEARCH, AIMessage(DRAFT), AIMessage("又一次附和 [D1]。")],
        [CheckResult(False, [ISSUE], {}), CheckResult(False, [ISSUE], {})],
    )
    assert seen == [DRAFT]  # the rewrite is not checked again
    assert res.answer == "又一次附和 [D1]。" and len(calls) == 3


def test_the_rewrite_still_goes_through_the_postcheck(
    monkeypatch: pytest.MonkeyPatch, fares
) -> None:  # type: ignore[no-untyped-def]
    res, _, _ = _ask(
        monkeypatch,
        fares,
        [SEARCH, AIMessage(DRAFT), AIMessage("需要 99,999 里程 [D7]。")],
        [CheckResult(False, [ISSUE], {})],
    )
    assert not res.postcheck.ok and res.postcheck.unknown_marks == ["D7"]
    assert res.answer.endswith(agent.POSTCHECK_WARNING["zh"])


def test_revise_note_lists_each_issue() -> None:
    note = revise_note([ISSUE])
    assert DRAFT in note and "unjustified_concession" in note


class Request:
    def __init__(self, messages: list[Any], tool_choice: Any = None) -> None:
        self.messages = messages
        self.tool_choice = tool_choice

    def override(self, **k: Any) -> Request:
        return Request(self.messages, k["tool_choice"])


def test_force_tool_applies_until_the_first_tool_result() -> None:
    force = agent.ForceTool("search_rules")
    got: list[Any] = []
    first = [HumanMessage("上文"), AIMessage("答"), HumanMessage("不是吧")]
    force.wrap_model_call(Request(first), lambda r: got.append(r.tool_choice))  # type: ignore[arg-type]
    after = [*first, SEARCH, ToolMessage("{}", tool_call_id="1")]
    force.wrap_model_call(Request(after), lambda r: got.append(r.tool_choice))  # type: ignore[arg-type]
    assert got == ["search_rules", None]


def test_a_follow_up_to_a_rule_answer_is_forced_to_search() -> None:
    assert agent.rule_follow_up(HISTORY)
    assert not agent.rule_follow_up([{"role": "assistant", "content": "110,000 里程"}])
    assert not agent.rule_follow_up(None)


def test_check_sees_the_conversation_and_decides_in_code(monkeypatch: pytest.MonkeyPatch) -> None:
    from milewise import check
    from milewise.config import Settings

    prompts: list[str] = []

    def structured(*a: Any, **k: Any) -> Any:
        prompts.append(k["prompt"])
        assert k["temperature"] == 0
        return {"issues": [ISSUE, {"sentence": "x", "kind": "style", "problem": "p"}]}, {}

    monkeypatch.setattr(check.llm, "structured", structured)
    history = [
        {"role": "user", "content": "第一问：马尼拉出发的票"},
        {"role": "assistant", "content": "第一答"},
        {"role": "user", "content": "香港出发的行程呢"},
        {"role": "assistant", "content": "上一答"},
    ]
    ev = {"D1": {**CHUNK, "editor_note": "Milewise 注"}, "C1": {"itinerary_facts": {"n": 4}}}
    res = check.check_answer(DRAFT, "不是吧", history, ev, Settings())
    assert not res.ok and res.issues == [ISSUE]  # an unknown kind is dropped
    p = prompts[0]
    for part in ["第一问", "香港出发的行程呢", "上一答", "不是吧", "Milewise 注", '"n": 4', DRAFT]:
        assert part in p
    assert "第一答" not in p
