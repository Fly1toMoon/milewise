"""Check an answer to a rule follow-up before it stands (design.md §4.13, step 6).

Runs only when the conversation has earlier turns and this turn called search_rules: those
are the answers that went wrong in multi-05〜08 with the right excerpts in hand (agreeing
with a user's reading the text does not support, stating rules the excerpts do not contain,
contradicting a ticket the user reported). The check model reads the answer sentence by
sentence against this turn's excerpts, their editor notes and itinerary_facts, the first
question of the conversation and the previous answer; ask() rewrites the answer once with
the problems it finds. Whether an itinerary is allowed is still never decided in code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from milewise import llm
from milewise.config import Settings

KINDS = ("unsupported", "conflicts_with_ticketed", "unjustified_concession")
CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sentence": {"type": "string"},
                    "kind": {"type": "string", "enum": list(KINDS)},
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
an excerpt may carry an editor_note, which is Milewise's own note, not the airline's), \
itinerary_facts (counts computed by code), and the answer.

Go through the answer sentence by sentence and report only these problems:
- unsupported: the sentence states a rule or condition as the airline's that no excerpt \
says, reads an excerpt as saying more than it does, or gives counts, countries or an origin \
country that disagree with itinerary_facts. Saying that the official text does not define \
a point, giving the possible readings and suggesting to confirm with the airline is \
supported; so is relaying an editor_note as Milewise's note.
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
REVISE_NOTE = """\
[Milewise review, not from the user] Your answer above was checked against the excerpts \
and the conversation, and these sentences have problems:
{issues}
Write the whole answer to the user's last question again, fixing them and keeping what \
was right. Follow the system prompt as before (rules 13, 17 and 18): cite evidence ids \
from this turn, do not just agree with the user, and say so where the official text does \
not settle a point. Use the excerpts you already have; call search_rules again only if you \
need text you do not have. Do not mention this review."""

# The previous answer and the first question are shown up to this many characters each.
CONTEXT_CHARS = 3000


@dataclass
class CheckResult:
    ok: bool
    issues: list[dict[str, str]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)


def should_check(history: list[dict[str, Any]] | None, tool_names: list[str]) -> bool:
    """Only follow-ups that looked rules up are checked; other questions cost nothing more."""
    has_history = any(m.get("role") == "assistant" for m in history or [])
    return has_history and "search_rules" in tool_names


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


def check_answer(
    answer: str,
    question: str,
    history: list[dict[str, Any]],
    evidence: dict[str, dict[str, Any]],
    settings: Settings,
) -> CheckResult:
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
        {
            "sentence": str(i.get("sentence", "")),
            "kind": i["kind"],
            "problem": str(i.get("problem", "")),
        }
        for i in out.get("issues") or []
        if isinstance(i, dict) and i.get("kind") in KINDS
    ]
    return CheckResult(not issues, issues, usage)


def revise_note(issues: list[dict[str, str]]) -> str:
    lines = "\n".join(f'- ({i["kind"]}) "{i["sentence"]}": {i["problem"]}' for i in issues)
    return REVISE_NOTE.format(issues=lines)
