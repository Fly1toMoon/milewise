"""Verify an answer against this turn's evidence before it stands (design.md §4.13).

One entry point, `verify()`, one result: a list of `Issue`s, each saying where it came from
(`code` or `model`), what it is about (the value or sentence of the answer) and why.

* code: every miles figure, evidence id and URL in the answer must come from this turn's
  tool results (Evidence). Always run; costs nothing.
* model: on a follow-up that looked rules up (the conversation has an earlier answer and
  this turn called search_rules), a check model reads the answer sentence by sentence
  against this turn's excerpts, their editor notes and itinerary_facts, the first question
  and the previous answer. Those are the answers that went wrong in multi-05〜08 with the
  right excerpts in hand. Whether an itinerary is allowed is still never decided in code.

ask() rewrites an answer with issues once, giving the model `revise_note(issues)`, then runs
only the code checks again; what they still find is shown as POSTCHECK_WARNING.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from milewise import llm
from milewise.config import Settings, cost_usd
from milewise.evidence import URL_RE, Evidence, as_int

_MILES_NUMBER = re.compile(
    r"(\d{1,3}(?:[,，]\d{3})+|\d{4,})\s*(?:マイル|miles?|英里|里程|哩)", re.IGNORECASE
)
_EVIDENCE_MARK = re.compile(r"\[([CD]\d+)\]")

POSTCHECK_WARNING = {
    "ja": "\n\n⚠️ 自動チェック: 根拠データにない数値・引用・リンクが含まれています。公式サイトで必ずご確認ください。",
    "zh": "\n\n⚠️ 自动检查：回答中含有依据数据之外的数字、引用或链接，请务必以官网为准。",
    "en": "\n\n⚠️ Automatic check: this answer contains numbers, citations or links not backed by the source data. Please verify on the official site.",
}
# Appended when the answer looked nothing up at all (also after the retry, see ground.py).
UNCHECKED_NOTE = {
    "ja": "\n\nℹ️ この回答は公式資料を参照していません。",
    "zh": "\n\nℹ️ 本回答没有查阅官方资料。",
    "en": "\n\nℹ️ This answer was not checked against the official sources.",
}


@dataclass(frozen=True)
class Issue:
    source: Literal["code", "model"]
    kind: str
    quote: str  # the value or sentence of the answer it is about
    problem: str


@dataclass
class Verdict:
    issues: list[Issue] = field(default_factory=list)
    model_checked: bool = False
    cost_usd: float = 0.0  # the check model's call


def code_issues(answer: str, evidence: Evidence) -> list[Issue]:
    """No miles figure, citation or link that this turn's tools did not produce."""
    out = [
        Issue("code", "number", m.group(0), "this figure is not in this turn's tool results")
        for m in _MILES_NUMBER.finditer(answer)
        if as_int(m.group(1)) not in evidence.allowed_miles
    ]
    out += [
        Issue("code", "citation", f"[{m}]", "no evidence with this id in this turn")
        for m in _EVIDENCE_MARK.findall(answer)
        if m not in evidence.items
    ]
    out += [
        Issue("code", "url", u, "this link is not in this turn's tool results")
        for u in (u.rstrip(".:") for u in URL_RE.findall(answer))
        if u not in evidence.allowed_urls
    ]
    return out


def needs_model_check(history: list[dict[str, Any]] | None, tools_called: list[str]) -> bool:
    """Only follow-ups that looked rules up are checked by a model; others cost nothing more."""
    has_history = any(m.get("role") == "assistant" for m in history or [])
    return has_history and "search_rules" in tools_called


def verify(
    answer: str,
    evidence: Evidence,
    *,
    question: str,
    history: list[dict[str, Any]] | None,
    tools_called: list[str],
    settings: Settings,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
) -> Verdict:
    verdict = Verdict(code_issues(answer, evidence))
    if needs_model_check(history, tools_called):
        if on_event:
            on_event("status", {"stage": "checking"})
        issues, usage = model_issues(answer, question, history or [], evidence.items, settings)
        verdict.issues += issues
        verdict.model_checked = True
        verdict.cost_usd = cost_usd(
            settings.check_model, usage.get("input_tokens", 0), 0, usage.get("output_tokens", 0)
        )
    return verdict


# ---------- the model check ----------

MODEL_KINDS = ("unsupported", "conflicts_with_ticketed", "unjustified_concession")
CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sentence": {"type": "string"},
                    "kind": {"type": "string", "enum": list(MODEL_KINDS)},
                    "problem": {"type": "string"},
                },
                "required": ["sentence", "kind", "problem"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["issues"],
    "additionalProperties": False,
}
CHECK_INSTRUCTIONS = """\
You review an answer written by Milewise, an assistant for ANA / JAL award-ticket rules, \
before the user sees it. You get the conversation's first question, the previous question \
and answer, the user's new question, the rule excerpts this answer was allowed to use (official text; \
an excerpt may carry an editor_note, which is Milewise's own note on a point the official \
text leaves open, not the airline's), itinerary_facts (counts computed by code), and the answer.

Go through the answer sentence by sentence and report only these problems:
- unsupported: the sentence states a rule or condition as the airline's that no excerpt \
says, reads an excerpt as saying more than it does, or gives counts, countries or an origin \
country that disagree with itinerary_facts. Also unsupported: an editor_note says the \
official text does not define a point, and the answer's conclusion depends on that point \
but is stated as definite (surely allowed / surely not allowed). Saying that the official \
text does not define a point, giving the possible readings and suggesting to confirm with \
the airline is supported.
- conflicts_with_ticketed: a conclusion that contradicts an itinerary the user reported as \
actually ticketed (e.g. in a post quoted in the first question), without pointing out the \
conflict.
- unjustified_concession: the user disputed the previous answer or offered their own \
reading, and the answer agrees (e.g. "you're right", "你说得对") or changes its conclusion \
although the excerpts do not support the user's reading, including when the user treats a \
term the excerpts leave undefined as settled.

Quote each sentence exactly as it appears in the answer. Return an empty list when there \
is no such problem. Do not report style, length, language, missing details or anything \
else.
"""
# The previous answer and the first question are shown up to this many characters each.
CONTEXT_CHARS = 3000


def _excerpts(evidence: dict[str, dict[str, Any]]) -> str:
    parts = []
    for eid, v in evidence.items():
        if eid.startswith("D"):
            note = f"\neditor_note: {v['editor_note']}" if v.get("editor_note") else ""
            parts.append(f"[{eid}] section: {v.get('section', '')}\n{v['text']}{note}")
    return "\n\n".join(parts) or "(none)"


def _facts(evidence: dict[str, dict[str, Any]]) -> str:
    facts = [v["itinerary_facts"] for v in evidence.values() if v.get("itinerary_facts")]
    return json.dumps(facts, ensure_ascii=False) if facts else "(none in this turn)"


def model_issues(
    answer: str,
    question: str,
    history: list[dict[str, Any]],
    evidence: dict[str, dict[str, Any]],
    settings: Settings,
) -> tuple[list[Issue], dict[str, int]]:
    turns = [m for m in history if m.get("role") in ("user", "assistant")]
    users = [str(m.get("content") or "") for m in turns if m["role"] == "user"]
    answers = [str(m.get("content") or "") for m in turns if m["role"] == "assistant"]
    first = users[0] if users else ""
    # the question the previous answer replied to, when it is not the first one
    last_q = users[-1] if len(users) > 1 else ""
    prompt = (
        f"First question of the conversation:\n{first[:CONTEXT_CHARS]}\n\n"
        + (f"Previous question:\n{last_q[:CONTEXT_CHARS]}\n\n" if last_q else "")
        + f"Previous answer:\n{(answers[-1] if answers else '')[:CONTEXT_CHARS]}\n\n"
        f"User's new question:\n{question}\n\n"
        f"Rule excerpts of this turn:\n{_excerpts(evidence)}\n\n"
        f"itinerary_facts of this turn:\n{_facts(evidence)}\n\n"
        f"Answer to review:\n{answer}"
    )
    out, usage = llm.structured(
        settings.check_model,
        name="check_answer",
        instructions=CHECK_INSTRUCTIONS,
        prompt=prompt,
        schema=CHECK_SCHEMA,
        s=settings,
        max_tokens=1500,
        temperature=0,
    )
    issues = [
        Issue("model", i["kind"], str(i.get("sentence", "")), str(i.get("problem", "")))
        for i in out.get("issues") or []
        if isinstance(i, dict) and i.get("kind") in MODEL_KINDS
    ]
    return issues, usage


# ---------- the rewrite ----------

REVISE_NOTE = """\
[Milewise review, not from the user] Your answer above was checked against this turn's \
tool results and the conversation, and these parts have problems:
{issues}
Write the whole answer to the user's last question again, fixing them and keeping what \
was right. Follow the system prompt as before: every figure, evidence id and link comes \
from this turn's tool results (rules 2, 7 and 13), only what the excerpts say is stated as \
a rule, and where they do not settle a point you say so (rule 18). Use the results you \
already have; call a tool again only if you need something you do not have. Do not \
mention this review."""


def revise_note(issues: list[Issue]) -> str:
    lines = "\n".join(f'- ({i.source}: {i.kind}) "{i.quote}": {i.problem}' for i in issues)
    return REVISE_NOTE.format(issues=lines)
