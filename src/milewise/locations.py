"""City / airport resolution for user input in Japanese, Chinese or English.

Exact matching only (after normalisation): a wrong city silently produces a wrong
fare, so anything not in data/locations/cities.csv is reported as unknown instead
of being guessed.
"""

from __future__ import annotations

import csv
import unicodedata
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from milewise.config import data_dir


@dataclass(frozen=True)
class City:
    city_code: str
    country: str
    name_ja: str
    name_en: str
    name_zh: str
    aliases: tuple[str, ...]
    airports: tuple[str, ...]


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).strip().lower()
    for suffix in ("国際空港", "国际机场", "國際機場", "airport", "空港", "机场", "機場", "市"):
        if text.endswith(suffix) and len(text) > len(suffix):
            text = text[: -len(suffix)].strip()
    return text


@cache
def load_cities(path: Path | None = None) -> tuple[City, ...]:
    path = path or data_dir() / "locations" / "cities.csv"
    with path.open(encoding="utf-8") as f:
        return tuple(
            City(
                city_code=row["city_code"],
                country=row["country"],
                name_ja=row["name_ja"],
                name_en=row["name_en"],
                name_zh=row["name_zh"],
                aliases=tuple(a for a in row["aliases"].split("|") if a),
                airports=tuple(a for a in row["airports"].split("|") if a),
            )
            for row in csv.DictReader(f)
        )


@cache
def _index() -> dict[str, frozenset[str]]:
    idx: dict[str, set[str]] = {}
    for c in load_cities():
        keys = [c.city_code, c.name_ja, c.name_en, c.name_zh, *c.aliases, *c.airports]
        for k in keys:
            idx.setdefault(normalize(k), set()).add(c.city_code)
    return {k: frozenset(v) for k, v in idx.items()}


def resolve_city(query: str) -> list[City]:
    """Return every city the query could mean: [] unknown, 1 resolved, >1 ambiguous."""
    codes = _index().get(normalize(query), frozenset())
    return [c for c in load_cities() if c.city_code in codes]
