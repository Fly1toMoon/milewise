"""Editor notes on rule sentences the official text leaves open (data/rule_notes.yaml, §4.13).

A note belongs to one sentence of one section of one page. search_rules attaches it to an
excerpt that contains that sentence (`editor_note`); the index build fails when a note's
sentence is no longer on its page, so a note cannot outlive what it is about.

Each note has a text per answer language (ja / zh / en). The model sees the one of its
answer language in the excerpt; the system shows the same text at the end of an answer that
cites the excerpt (`notes_block`), marked as Milewise's note, and the page shows it in the
source details. `strip_notes_block` takes it out again when the answer comes back as history.

A note with `when_itinerary: <flag>` is attached only when an itinerary of the conversation
has that flag in its itinerary_facts (e.g. cn_with_hk_mo_tw): the note on 出発国 pulled the
model onto that rule for trips it does not concern (multi-06, 2026-11-12). Which countries
an itinerary has is decided by itinerary_facts alone, not here.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from milewise.config import data_dir
from milewise.tools.miles import ITINERARY_FLAGS

LANGS = ("ja", "zh", "en")
# Heads the notes shown at the end of an answer; the page labels notes in its source details
# the same way.
NOTES_HEADER = {
    "ja": "📝 Milewise 注（航空会社の規定ではありません）",
    "zh": "📝 Milewise 注（非航司条文）",
    "en": "📝 Milewise note (not the airline's rules)",
}
_NOTES_BLOCK = re.compile(
    r"\n*(?:" + "|".join(re.escape(h) for h in NOTES_HEADER.values()) + r")\n.*\Z", re.S
)
_CITED_DOC = re.compile(r"\[(D\d+)\]")
# YAML folds a multi-line note with spaces; CJK sentences need none
_CJK_FOLD = re.compile(r"(?<=[。、，）])\s+")


def notes_path() -> Path:
    return data_dir() / "rule_notes.yaml"


@cache
def load_notes(path: Path | None = None) -> tuple[dict[str, Any], ...]:
    p = path or notes_path()
    if not p.exists():
        return ()
    notes = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("notes") or []
    for n in notes:
        missing = [k for k in ("id", "url", "section", "match", "note") if not n.get(k)]
        if missing:
            raise ValueError(f"rule note {n.get('id', '?')}: missing {missing}")
        text = n["note"] if isinstance(n["note"], dict) else {}
        no_lang = [lang for lang in LANGS if not str(text.get(lang) or "").strip()]
        if no_lang:
            raise ValueError(f"rule note {n['id']}: note needs a text in {no_lang}")
        n["note"] = {lang: _CJK_FOLD.sub("", str(text[lang]).strip()) for lang in LANGS}
        when = n.get("when_itinerary")
        if when is not None and when not in ITINERARY_FLAGS:
            raise ValueError(f"rule note {n['id']}: unknown when_itinerary {when!r}")
    return tuple(notes)


def _applies(note: dict[str, Any], url: str, section: str, text: str) -> bool:
    return note["url"] == url and note["section"] in section and note["match"] in text


def notes_for(
    url: str,
    section: str,
    text: str,
    notes: tuple[dict[str, Any], ...] | None = None,
    itinerary_flags: frozenset[str] | set[str] = frozenset(),
) -> list[dict[str, Any]]:
    """The notes whose page, section and sentence are those of this excerpt, and whose
    `when_itinerary` flag (if any) is among the conversation's `itinerary_flags`."""
    return [
        n
        for n in (load_notes() if notes is None else notes)
        if _applies(n, url, section, text)
        and (n.get("when_itinerary") is None or n["when_itinerary"] in itinerary_flags)
    ]


def dangling(chunks: list[dict[str, Any]], notes: tuple[dict[str, Any], ...]) -> list[str]:
    """Ids of notes on a page in `chunks` whose sentence no chunk of that section contains.

    Notes on pages that are not in `chunks` are not checked (e.g. a test index)."""
    urls = {c["url"] for c in chunks}
    out = []
    for n in notes:
        if n["url"] not in urls:
            continue
        if not any(
            _applies(n, c["url"], " > ".join(c.get("section_path", [])[1:]), c["text"])
            for c in chunks
        ):
            out.append(n["id"])
    return out


def notes_block(answer: str, evidence: dict[str, dict[str, Any]], lang: str) -> str:
    """The editor notes of the excerpts `answer` cites, each once with the ids that carry it,
    under NOTES_HEADER: appended to the answer by the system, not written by the model.

    `evidence` items carry their notes in the answer language, one per line."""
    by_note: dict[str, list[str]] = {}
    for eid in dict.fromkeys(_CITED_DOC.findall(answer)):
        for note in str((evidence.get(eid) or {}).get("editor_note") or "").splitlines():
            if note.strip():
                by_note.setdefault(note.strip(), []).append(eid)
    if not by_note:
        return ""
    lines = [f"- {''.join(f'[{e}]' for e in ids)} {note}" for note, ids in by_note.items()]
    return "\n\n" + NOTES_HEADER[lang] + "\n" + "\n".join(lines)


def strip_notes_block(answer: str) -> str:
    return _NOTES_BLOCK.sub("", answer)
