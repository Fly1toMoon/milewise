"""Grading for one eval case (design.md §4).

* miles figures are checked deterministically against the hand-verified gold numbers;
* rules / abstain / not-offered answers go to an LLM judge with a key-point rubric
  (must_mention / must_not_say), which is more stable than a holistic score (R10);
* citation accuracy: some cited source URL is one of the case's gold URLs.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from milewise import llm
from milewise.agent import URL_RE, AskResult
from milewise.config import Settings

_NUMBER_WITH_UNIT = re.compile(
    r"(\d{1,3}(?:[,，]\d{3})+|\d{4,})\s*(?:マイル|miles?|英里|里程|哩)", re.IGNORECASE
)
_MARK = re.compile(r"\[([CD]\d+)\]")

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "passed": {"type": "boolean"},
        "missing_points": {"type": "array", "items": {"type": "string"}},
        "violations": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
    },
    "required": ["passed", "missing_points", "violations", "rationale"],
    "additionalProperties": False,
}

JUDGE_INSTRUCTIONS = """\
You grade answers from an airline award-ticket assistant against a rubric written from the \
airline's official pages. The answer may be in Japanese, Chinese or English; judge meaning, \
not wording. Pass only if every required point is clearly stated (or an equivalent, more \
specific statement is made) and no forbidden statement appears. Extra correct detail is fine. \
Ignore citation markers like [D1] and the Sources section."""


@dataclass
class Grade:
    miles_ok: bool | None
    judge_ok: bool | None
    judge_rationale: str | None
    citation_ok: bool | None
    cited_urls: list[str]
    judge_usage: dict[str, int]

    @property
    def passed(self) -> bool:
        checks = [c for c in (self.miles_ok, self.judge_ok) if c is not None]
        return bool(checks) and all(checks)


def figures(answer: str) -> set[int]:
    return {int(re.sub(r"[,，]", "", m)) for m in _NUMBER_WITH_UNIT.findall(answer)}


def cited_urls(res: AskResult) -> list[str]:
    urls = set(URL_RE.findall(res.answer))
    for eid in _MARK.findall(res.answer):
        ev = res.evidence.get(eid, {})
        url = ev.get("url") or ev.get("chart", {}).get("source_url")
        if url:
            urls.add(url)
    return sorted(u.rstrip(".:") for u in urls)


def _rubric(case: dict[str, Any]) -> str | None:
    exp = case["expected"]
    if case["category"] == "abstain":
        rubric = (
            "The assistant must decline, or clearly say it cannot confirm the answer, because: "
            f"{exp['reason']}."
        )
        if not exp.get("allow_figures"):
            rubric += (
                " It must not state a specific required-miles figure for the asked itinerary "
                "(figures it explicitly labels as belonging to something else are allowed)."
            )
        return rubric
    if exp.get("not_offered"):
        return (
            "The assistant must say that this cabin is not offered as an award on this route "
            "(設定なし / not available), and must not state a required-miles figure for it."
        )
    if "must_mention" in exp:
        return (
            f"Reference answer: {exp.get('reference', '')}\n"
            f"Required points: {json.dumps(exp['must_mention'], ensure_ascii=False)}\n"
            f"Forbidden statements: {json.dumps(exp.get('must_not_say', []), ensure_ascii=False)}"
        )
    return None


def judge(
    case: dict[str, Any], answer: str, s: Settings, model: str
) -> tuple[bool, str, dict[str, int]]:
    out, usage = llm.structured(
        model,
        name="grade",
        instructions=JUDGE_INSTRUCTIONS,
        prompt=f"{_earlier(case)}Question: {case['question']}\n\nRubric:\n{_rubric(case)}\n\nAnswer to grade:\n{answer}",
        schema=JUDGE_SCHEMA,
        s=s,
    )
    notes = str(out.get("rationale", ""))
    if out.get("missing_points"):
        notes += f" | missing: {out['missing_points']}"
    if out.get("violations"):
        notes += f" | violations: {out['violations']}"
    return bool(out.get("passed")), notes, usage


def grade(case: dict[str, Any], res: AskResult, s: Settings, judge_model: str) -> Grade:
    exp = case["expected"]
    miles_ok: bool | None = None
    if exp.get("miles"):
        found = figures(res.answer)
        miles_ok = set(exp["miles"]) <= found and res.postcheck.ok

    judge_ok: bool | None = None
    rationale: str | None = None
    usage: dict[str, int] = {}
    if _rubric(case) is not None:
        judge_ok, rationale, usage = judge(case, res.answer, s, judge_model)

    urls = cited_urls(res)
    citation_ok = bool(set(urls) & set(case["gold_urls"])) if case["gold_urls"] else None
    return Grade(miles_ok, judge_ok, rationale, citation_ok, urls, usage)


def _earlier(case: dict[str, Any]) -> str:
    """Earlier turns of a follow-up case, so the judge knows what the question refers to."""
    turns = case.get("history") or []
    if not turns:
        return ""
    lines = "\n".join(f"{t['role']}: {t['content']}" for t in turns)
    return f"Earlier in the conversation:\n{lines}\n\n(The answer below is to the last question.)\n"
