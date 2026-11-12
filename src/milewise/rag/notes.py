"""Editor notes on rule sentences the official text leaves open (data/rule_notes.yaml, §4.13).

A note belongs to one sentence of one section of one page. search_rules attaches it to an
excerpt that contains that sentence (`editor_note`); the index build fails when a note's
sentence is no longer on its page, so a note cannot outlive what it is about.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import yaml

from milewise.config import data_dir


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
    return tuple(notes)


def _applies(note: dict[str, Any], url: str, section: str, text: str) -> bool:
    return note["url"] == url and note["section"] in section and note["match"] in text


def notes_for(
    url: str, section: str, text: str, notes: tuple[dict[str, Any], ...] | None = None
) -> list[dict[str, Any]]:
    """The notes whose page, section and sentence are those of this excerpt."""
    return [
        n for n in (load_notes() if notes is None else notes) if _applies(n, url, section, text)
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
