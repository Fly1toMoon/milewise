"""Eval grading: per-point judge verdicts, quote checks and the judge-free checks (§4.13)."""

from __future__ import annotations

from typing import Any

import pytest

from milewise.agent import AskResult
from milewise.config import Settings
from milewise.evals import grade as g
from milewise.evals.run import CaseResult, load_cases, summarize
from milewise.rag.index import RulesIndex, default_index_dir

CASE: dict[str, Any] = {
    "id": "t-1",
    "category": "rules",
    "lang": "zh",
    "question": "可以吗？",
    "gold_urls": [],
    "expected": {
        "reference": "参考",
        "must_mention": ["出发地和最终目的地可以在不同国家"],
        "must_not_say": ["全程需要同一个出发地和目的地"],
    },
}
ANSWER = "**可以**：出发地与最终目的地位于不同城市、甚至不同国家 [D5]。\n其他条件见下。"


def res(answer: str = ANSWER, evidence: dict[str, Any] | None = None, tools: Any = ()) -> AskResult:
    return AskResult(
        question="q",
        answer=answer,
        evidence=evidence or {},
        postcheck_ok=True,
        tool_calls=[{"name": t, "args": {}} for t in tools],
        usage={},
        cost_usd=0.0,
        latency_ms=0,
        model="m",
    )


def fake_judge(points: list[dict[str, Any]]) -> Any:
    def structured(*a: Any, **k: Any) -> tuple[dict[str, Any], dict[str, int]]:
        assert k["temperature"] == 0
        assert "M1: 出发地和最终目的地可以在不同国家" in k["prompt"]
        assert "F1: 全程需要同一个出发地和目的地" in k["prompt"]
        return {"points": points, "rationale": "r"}, {"input_tokens": 1, "output_tokens": 1}

    return structured


def test_quote_in_ignores_marks_spacing_width_and_markdown() -> None:
    assert g.quote_in(ANSWER, "可以：出发地与最终目的地位于不同城市、甚至不同国家。")
    assert g.quote_in(ANSWER, "出发地与最终目的地 … 甚至不同国家")  # pieces around an ellipsis
    assert not g.quote_in(ANSWER, "出发地和最终目的地可以在不同国家")  # a paraphrase
    assert not g.quote_in(ANSWER, "")


def test_pass_is_decided_in_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        g.llm,
        "structured",
        fake_judge(
            [
                {"id": "M1", "verdict": True, "quote": "甚至不同国家 [D5]"},
                {"id": "F1", "verdict": False, "quote": ""},
            ]
        ),
    )
    ok, _notes, points, _ = g.judge_points(CASE, ANSWER, Settings(), "bedrock:x")
    assert ok and [p.ok for p in points] == [True, True]


def test_required_point_with_invented_quote_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        g.llm,
        "structured",
        fake_judge(
            [
                {"id": "M1", "verdict": True, "quote": "出发地和最终目的地可以在不同国家"},
                {"id": "F1", "verdict": False, "quote": ""},
            ]
        ),
    )
    ok, notes, _, _ = g.judge_points(CASE, ANSWER, Settings(), "bedrock:x")
    assert not ok and "quote not in answer: ['M1']" in notes


def test_forbidden_point_or_missing_verdict_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        g.llm,
        "structured",
        fake_judge([{"id": "F1", "verdict": True, "quote": "其他条件见下"}]),
    )
    ok, notes, _points, _ = g.judge_points(CASE, ANSWER, Settings(), "bedrock:x")
    assert not ok
    assert "missing: ['M1']" in notes and "violations" in notes


def test_abstain_and_not_offered_become_points() -> None:
    ab = {"category": "abstain", "expected": {"reason": "not covered"}}
    _, pts = g.rubric_points(ab) or ("", [])
    assert [p.id for p in pts] == ["M1", "F1"]
    ab["expected"]["allow_figures"] = True
    _, pts = g.rubric_points(ab) or ("", [])
    assert [p.id for p in pts] == ["M1"]
    no = {"category": "miles", "expected": {"not_offered": True}}
    assert [p.id for p in (g.rubric_points(no) or ("", []))[1]] == ["M1", "F1"]
    assert g.rubric_points({"category": "miles", "expected": {"miles": [1]}}) is None


def test_deterministic_checks() -> None:
    case = {
        "expected": {
            "must_call": ["search_rules"],
            "gold_sections": ["旅程について", "利用区間・途中降機について"],
            "must_not_match": [r"^\W*(你说得对|您说得对)"],
        }
    }
    ev = {
        "D1": {"section": "旅程について", "url": "u"},
        "D2": {"section": "利用区間・途中降機について", "url": "u"},
    }
    assert g.deterministic_checks(case, res("不是这样。", ev, ["search_rules"])) == (True, [])
    ok, notes = g.deterministic_checks(case, res("**你说得对**，不行。", {"D1": ev["D1"]}))
    assert not ok
    assert notes == [
        "tool not called: search_rules",
        "section not in evidence: 利用区間・途中降機について",
        "answer matches /^\\W*(你说得对|您说得对)/",
    ]
    assert g.deterministic_checks({"expected": {}}, res()) == (None, [])
    # a remote run without sections cannot be checked
    remote = {"expected": {"gold_sections": ["旅程について"]}}
    assert g.deterministic_checks(remote, res(evidence={"D1": {"url": "u"}})) == (None, [])


def _result(case_id: str, passed: bool) -> CaseResult:
    return CaseResult(case_id, "rules", "zh", passed, False, "", [], None, True, 1, 0, 0, 0.0)


def test_summary_counts_passes_per_case() -> None:
    rs = [_result("a", True), _result("a", False), _result("a", True), _result("b", False)]
    assert summarize(rs)["per_case"] == {"a": "2/3", "b": "0/1"}


def test_history_evidence_ids_exist_in_the_index() -> None:
    """Chunk ids change with the page text: rebuild them in eval/cases.yaml when this fails."""
    if not (default_index_dir() / "manifest.json").exists():
        pytest.skip("no rules index (make index)")
    index = RulesIndex.load()
    ids = [
        (c["id"], cid)
        for c in load_cases()
        for turn in c.get("history") or []
        for cid in turn.get("evidence") or []
    ]
    assert ids, "multi-05..08 carry evidence ids"
    assert [x for x in ids if index.chunk(x[1]) is None] == []


def test_quote_in_tolerates_judge_slips_but_not_paraphrase() -> None:
    ref = "羽田出发 11,000、成田出发 9,000（基本里程，最低值）。"
    assert g.quote_in(ref, "羽田出发 11,000、成田出发 9,000（基本里程，最低値）。")  # 值 -> 値
    days = "7/1～7/15 和 8/24～8/31 是 Regular season， 7/16～8/23 是 High season。"
    assert g.quote_in(days, "7/1～7/15 是 Regular season")  # a skipped clause
    ja = '关键是"出発国へ戻り、さらに第三国へ行く旅程は組めません"（回到出发国后不能再去第三国）。'
    assert g.quote_in(ja, "关键是'出発国へ戻り、さらに第三国へ行く旅程は組めません'")
    long = ANSWER + "\n" + "其他内容。" * 50 + "不同国家"
    assert not g.quote_in(long, "可以在不同国家")  # characters scattered over the answer
