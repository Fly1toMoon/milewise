"""Grading for one eval case (design.md §4, §4.13).

* miles figures are checked deterministically against the hand-verified gold numbers;
* rules / abstain / not-offered answers go to an LLM judge that rules on each rubric point
  separately (must_mention / must_not_say) and quotes the sentence of the answer it relied
  on; the pass/fail decision is made here in code, not by the judge (R10);
* optional deterministic checks need no judge: tools that must be called (must_call), rule
  sections that must be in this turn's evidence (gold_sections) and patterns the answer
  must not match (must_not_match);
* citation accuracy: some cited source URL is one of the case's gold URLs.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from milewise import llm
from milewise.agent import URL_RE, AskResult, normalize_marks
from milewise.config import Settings

_NUMBER_WITH_UNIT = re.compile(
    r"(\d{1,3}(?:[,，]\d{3})+|\d{4,})\s*(?:マイル|miles?|英里|里程|哩)", re.IGNORECASE
)
_MARK = re.compile(r"\[([CD]\d+)\]")

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "points": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "verdict": {"type": "boolean"},
                    "quote": {"type": "string"},
                },
                "required": ["id", "verdict", "quote"],
                "additionalProperties": False,
            },
        },
        "rationale": {"type": "string"},
    },
    "required": ["points", "rationale"],
    "additionalProperties": False,
}

JUDGE_INSTRUCTIONS = """\
You grade answers from an airline award-ticket assistant against a rubric written from the \
airline's official pages. The answer may be in Japanese, Chinese or English; judge meaning, \
not wording. Extra correct detail is fine. Ignore citation markers like [D1].

Rule on every rubric point separately and return one item per point id:
- M points (required): verdict true only if the answer clearly states the point, or an \
equivalent or more specific statement. Implied or partial is false.
- F points (forbidden): verdict true if the answer makes that statement or one with the same \
meaning anywhere, including inside a longer sentence or a conditional that it presents as \
the rule. Mentioning it only to reject it, or as one possible reading the answer does not \
endorse, is false.
- quote: copy, character for character, the shortest sentence or phrase of the answer that \
your verdict relies on (for a true M point: where it is stated; for a true F point: where \
the forbidden statement is made). Leave it empty when the verdict is false. Do not \
translate, paraphrase or join pieces from different places.
Then give a one-paragraph rationale."""


@dataclass
class Point:
    id: str  # M1.. required, F1.. forbidden
    text: str
    verdict: bool | None = None  # what the judge said
    quote: str = ""
    quote_found: bool = False

    @property
    def ok(self) -> bool:
        if self.id.startswith("M"):
            # a claimed match must be backed by a real sentence of the answer
            return bool(self.verdict) and self.quote_found
        return not self.verdict


@dataclass
class Grade:
    miles_ok: bool | None
    judge_ok: bool | None
    judge_rationale: str | None
    citation_ok: bool | None
    cited_urls: list[str]
    judge_usage: dict[str, int]
    checks_ok: bool | None = None  # must_call / gold_sections / must_not_match
    check_notes: list[str] = field(default_factory=list)
    points: list[dict[str, Any]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        checks = [c for c in (self.miles_ok, self.judge_ok, self.checks_ok) if c is not None]
        return bool(checks) and all(checks)


def figures(answer: str) -> set[int]:
    return {int(re.sub(r"[,，]", "", m)) for m in _NUMBER_WITH_UNIT.findall(answer)}


def cited_urls(res: AskResult) -> list[str]:
    urls = set(URL_RE.findall(res.answer))
    for eid in _MARK.findall(normalize_marks(res.answer)):
        ev = res.evidence.get(eid, {})
        url = ev.get("url") or ev.get("chart", {}).get("source_url")
        if url:
            urls.add(url)
    return sorted(u.rstrip(".:") for u in urls)


def rubric_points(case: dict[str, Any]) -> tuple[str, list[Point]] | None:
    """(context for the judge, the points to rule on), or None for cases without a rubric."""
    exp = case["expected"]
    if case["category"] == "abstain":
        points = [
            Point(
                "M1",
                "The assistant declines, or clearly says it cannot confirm or answer this "
                f"(it need not give the reason; background on why: {exp['reason']})",
            )
        ]
        if not exp.get("allow_figures"):
            points.append(
                Point(
                    "F1",
                    "States a specific required-miles figure for the asked itinerary (figures "
                    "it explicitly labels as belonging to something else do not count)",
                )
            )
        return "", points
    if exp.get("not_offered"):
        return "", [
            Point(
                "M1",
                "Says this cabin is not offered as an award on this route "
                "(設定なし / not available)",
            ),
            Point("F1", "States a required-miles figure for this cabin on this route"),
        ]
    if "must_mention" in exp:
        points = [Point(f"M{i}", p) for i, p in enumerate(exp["must_mention"], 1)]
        points += [Point(f"F{i}", p) for i, p in enumerate(exp.get("must_not_say", []), 1)]
        return f"Reference answer (background only): {exp.get('reference', '')}\n", points
    return None


_ELLIPSIS = re.compile(r"…+|\.{3,}")
# Quotes count when nearly every character of a piece is found, in order, within a stretch
# of the answer not much longer than the piece: judges swap quote marks, "correct" 值 to 値
# and skip a clause ("7/1～7/15 和 8/24～8/31 是" -> "7/1～7/15 是"), but a paraphrase or a
# point the answer does not make shares far fewer characters (§4.13).
QUOTE_MATCH = 0.9
QUOTE_SPAN = 2.0


def _norm(text: str) -> str:
    """NFKC, without citation marks, whitespace, punctuation and markdown symbols."""
    text = unicodedata.normalize("NFKC", re.sub(r"\[[CD]\d+\]", "", normalize_marks(text)))
    return "".join(
        ch
        for ch in text
        if not ch.isspace() and not unicodedata.category(ch).startswith("P") and ch not in "*`>|"
    )


def _piece_in(piece: str, answer: str) -> bool:
    if piece in answer:
        return True
    blocks = [
        b
        for b in SequenceMatcher(None, piece, answer, autojunk=False).get_matching_blocks()
        if b.size
    ]
    if not blocks:
        return False
    matched = sum(b.size for b in blocks)
    span = blocks[-1].b + blocks[-1].size - blocks[0].b
    return matched >= QUOTE_MATCH * len(piece) and span <= QUOTE_SPAN * len(piece)


def quote_in(answer: str, quote: str) -> bool:
    """The quote (pieces separated by an ellipsis) appears in the answer, ignoring spacing,
    width, punctuation, markdown and citation marks, and tolerating a few changed or skipped
    characters (QUOTE_MATCH)."""
    pieces = [p for p in (_norm(q) for q in _ELLIPSIS.split(quote)) if p]
    a = _norm(answer)
    return bool(pieces) and all(_piece_in(p, a) for p in pieces)


def judge_points(
    case: dict[str, Any], answer: str, s: Settings, model: str
) -> tuple[bool, str, list[Point], dict[str, int]]:
    rub = rubric_points(case)
    assert rub is not None
    context, points = rub
    listing = "\n".join(f"{p.id}: {p.text}" for p in points)
    out, usage = llm.structured(
        model,
        name="grade",
        instructions=JUDGE_INSTRUCTIONS,
        prompt=f"{_earlier(case)}Question: {case['question']}\n\n{context}"
        f"Rubric points:\n{listing}\n\nAnswer to grade:\n{answer}",
        schema=JUDGE_SCHEMA,
        s=s,
        temperature=0,
    )
    by_id = {str(p.get("id", "")).strip(): p for p in out.get("points") or []}
    for p in points:
        got = by_id.get(p.id)
        if got is None:
            p.verdict = None  # not ruled on: a missing M point fails, a missing F point passes
            continue
        p.verdict = bool(got.get("verdict"))
        p.quote = str(got.get("quote") or "")
        p.quote_found = quote_in(answer, p.quote) if p.quote else False
    ok = all(p.ok for p in points)
    notes = str(out.get("rationale", ""))
    missing = [p.id for p in points if p.id.startswith("M") and not p.ok]
    unquoted = [p.id for p in points if p.id.startswith("M") and p.verdict and not p.quote_found]
    violations = [(p.id, p.quote) for p in points if p.id.startswith("F") and not p.ok]
    if missing:
        notes += f" | missing: {missing}" + (
            f" (quote not in answer: {unquoted})" if unquoted else ""
        )
    if violations:
        notes += f" | violations: {violations}"
    return ok, notes, points, usage


def judge(
    case: dict[str, Any], answer: str, s: Settings, model: str
) -> tuple[bool, str, dict[str, int]]:
    ok, notes, _, usage = judge_points(case, answer, s, model)
    return ok, notes, usage


def deterministic_checks(case: dict[str, Any], res: AskResult) -> tuple[bool | None, list[str]]:
    """must_call, gold_sections, must_not_match (no judge). None when the case has none."""
    exp = case["expected"]
    notes: list[str] = []
    ran = False
    if exp.get("must_call"):
        ran = True
        called = {t["name"] for t in res.tool_calls}
        notes += [f"tool not called: {t}" for t in exp["must_call"] if t not in called]
    if exp.get("gold_sections"):
        docs = [v for k, v in res.evidence.items() if k.startswith("D")]
        # a remote run only knows the URLs of its evidence: nothing to compare against
        if not docs or any("section" in v for v in docs):
            ran = True
            sections = [str(v.get("section", "")) for v in docs]
            notes += [
                f"section not in evidence: {g}"
                for g in exp["gold_sections"]
                if not any(g in sec for sec in sections)
            ]
    for pattern in exp.get("must_not_match") or []:
        ran = True
        if re.search(pattern, res.answer, re.MULTILINE):
            notes.append(f"answer matches /{pattern}/")
    return (not notes if ran else None), notes


def grade(case: dict[str, Any], res: AskResult, s: Settings, judge_model: str) -> Grade:
    exp = case["expected"]
    miles_ok: bool | None = None
    if exp.get("miles"):
        found = figures(res.answer)
        miles_ok = set(exp["miles"]) <= found and res.postcheck.ok

    judge_ok: bool | None = None
    rationale: str | None = None
    usage: dict[str, int] = {}
    points: list[Point] = []
    if rubric_points(case) is not None:
        judge_ok, rationale, points, usage = judge_points(case, res.answer, s, judge_model)

    checks_ok, check_notes = deterministic_checks(case, res)
    urls = cited_urls(res)
    citation_ok = bool(set(urls) & set(case["gold_urls"])) if case["gold_urls"] else None
    return Grade(
        miles_ok,
        judge_ok,
        rationale,
        citation_ok,
        urls,
        usage,
        checks_ok,
        check_notes,
        [
            {"id": p.id, "verdict": p.verdict, "quote": p.quote, "quote_found": p.quote_found}
            for p in points
        ],
    )


def _earlier(case: dict[str, Any]) -> str:
    """Earlier turns of a follow-up case, so the judge knows what the question refers to."""
    turns = case.get("history") or []
    if not turns:
        return ""
    lines = "\n".join(f"{t['role']}: {t['content']}" for t in turns)
    return f"Earlier in the conversation:\n{lines}\n\n(The answer below is to the last question.)\n"
