"""The system prompt and the notes that refer to its rules by number."""

from __future__ import annotations

import re

from milewise.ground import RETRY_NOTE
from milewise.prompt import SYSTEM_PROMPT
from milewise.verify import REVISE_NOTE


def _rules(text: str) -> set[int]:
    return {
        int(n)
        for m in re.finditer(r"rules? ((?:\d+(?:, | and )?)+)", text)
        for n in re.findall(r"\d+", m.group(1))
    }


def test_rule_numbers_referred_to_exist() -> None:
    numbered = {int(n) for n in re.findall(r"^(\d+)\. ", SYSTEM_PROMPT, re.M)}
    assert numbered == set(range(1, 19))
    for text in (SYSTEM_PROMPT, RETRY_NOTE, REVISE_NOTE):
        assert _rules(text) <= numbered, text


def test_rule_notes_are_shown_by_the_system_not_relayed() -> None:
    assert "the system shows it to the user" in " ".join(SYSTEM_PROMPT.split())
    assert "relay" not in SYSTEM_PROMPT
