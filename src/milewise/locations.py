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


# Airport-level names, for routes priced per airport (JAL: 羽田 vs 成田 to Taipei, 虹橋 vs
# 浦東 for Shanghai). City-level aliases in cities.csv stay as they are.
AIRPORT_ALIASES = {
    "羽田": "HND",
    "haneda": "HND",
    "成田": "NRT",
    "narita": "NRT",
    "関西": "KIX",
    "关西": "KIX",
    "kansai": "KIX",
    "伊丹": "ITM",
    "itami": "ITM",
    "中部": "NGO",
    "セントレア": "NGO",
    "chubu": "NGO",
    "centrair": "NGO",
    "那覇": "OKA",
    "那霸": "OKA",
    "naha": "OKA",
    "虹橋": "SHA",
    "虹桥": "SHA",
    "hongqiao": "SHA",
    "浦東": "PVG",
    "浦东": "PVG",
    "pudong": "PVG",
    "松山": "TSA",
    "songshan": "TSA",
    "桃園": "TPE",
    "桃园": "TPE",
    "taoyuan": "TPE",
    "仁川": "ICN",
    "incheon": "ICN",
    "金浦": "GMP",
    "gimpo": "GMP",
    "スワンナプーム": "BKK",
    "suvarnabhumi": "BKK",
    "ドンムアン": "DMK",
    "don mueang": "DMK",
}


def airport_hint(query: str) -> str | None:
    """The specific airport a query names (IATA code or airport name), if any."""
    q = normalize(query)
    if q in AIRPORT_ALIASES:
        return AIRPORT_ALIASES[q]
    code = q.upper()
    if len(code) == 3 and any(code in c.airports for c in load_cities()):
        return code
    return None


@dataclass(frozen=True)
class Area:
    """A country or region a user names instead of a city ("中国到欧洲")."""

    code: str  # ISO country code, or a region code such as EUROPE
    kind: str  # country | region
    name_ja: str


@cache
def _areas() -> dict[str, Area]:
    path = data_dir() / "locations" / "areas.csv"
    idx: dict[str, Area] = {}
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            area = Area(row["code"], row["kind"], row["name_ja"])
            names = [row["name_ja"], row["name_en"], row["name_zh"], *row["aliases"].split("|")]
            for n in names:
                if n:
                    idx[normalize(n)] = area
    return idx


def resolve_area(query: str) -> Area | None:
    """Country or region for zone-based charts; cities always win (see resolve_city)."""
    return _areas().get(normalize(query))


@cache
def airport_coords() -> dict[str, tuple[float, float]]:
    """IATA code -> (lat, lon) from data/locations/airports.csv (OurAirports, public domain)."""
    path = data_dir() / "locations" / "airports.csv"
    with path.open(encoding="utf-8") as f:
        lines = [line for line in f if not line.startswith("#")]
    return {r["iata"]: (float(r["lat"]), float(r["lon"])) for r in csv.DictReader(lines)}


def great_circle_miles(a: str, b: str) -> int | None:
    """Great-circle distance in statute miles between two airports, or None if unknown."""
    from math import asin, cos, radians, sin, sqrt

    coords = airport_coords()
    if a not in coords or b not in coords:
        return None
    (la1, lo1), (la2, lo2) = coords[a], coords[b]
    h = (
        sin(radians(la2 - la1) / 2) ** 2
        + cos(radians(la1)) * cos(radians(la2)) * sin(radians(lo2 - lo1) / 2) ** 2
    )
    return round(2 * 3958.8 * asin(sqrt(h)))
