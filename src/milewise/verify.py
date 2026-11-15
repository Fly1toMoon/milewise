"""Verify an answer against this turn's evidence before it stands (design.md §4.13).

Every miles figure, evidence id and URL in the answer must come from this turn's tool
results (Evidence). `verify()` returns what does not, as `Issue`s. ask() rewrites an answer
with issues once, giving the model `revise_note(issues)`, checks the rewrite again, and shows
POSTCHECK_WARNING under what still fails. Whether an itinerary is allowed is never decided
here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

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
    kind: str  # number | citation | url
    quote: str  # the value in the answer it is about
    problem: str


def verify(answer: str, evidence: Evidence) -> list[Issue]:
    """No miles figure, citation or link that this turn's tools did not produce."""
    out = [
        Issue("number", m.group(0), "this figure is not in this turn's tool results")
        for m in _MILES_NUMBER.finditer(answer)
        if as_int(m.group(1)) not in evidence.allowed_miles
    ]
    out += [
        Issue("citation", f"[{m}]", "no evidence with this id in this turn")
        for m in _EVIDENCE_MARK.findall(answer)
        if m not in evidence.items
    ]
    out += [
        Issue("url", u, "this link is not in this turn's tool results")
        for u in (u.rstrip(".:") for u in URL_RE.findall(answer))
        if u not in evidence.allowed_urls
    ]
    return out


REVISE_NOTE = """\
[Milewise check, not from the user] These values in your answer above are not in this \
turn's tool results:
{issues}
Write the whole answer to the user's last question again, keeping what was right: every \
figure, evidence id and link comes from this turn's tool results (rules 2, 7 and 13). Use \
the results you already have; call a tool again only if you need something you do not \
have. Do not mention this check."""


def revise_note(issues: list[Issue]) -> str:
    lines = "\n".join(f'- ({i.kind}) "{i.quote}": {i.problem}' for i in issues)
    return REVISE_NOTE.format(issues=lines)
