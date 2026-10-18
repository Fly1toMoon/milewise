"""Award charts in memory, read straight from the committed CSVs (design.md §4.6).

data/charts/<family>/ is the single source of truth, so there is no load step: the
files ship in the image and are parsed once per process. The queries the tools need
(zone of a city, season of a date, rates of a zone pair or route) are plain filters.
"""

from __future__ import annotations

import csv
import itertools
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from milewise.config import data_dir

SEASON_ORDER = {"L": 0, "R": 1, "H": 2, "ANY": 3}


@dataclass(frozen=True)
class Chart:
    chart_id: str
    family: str
    airline: str
    trip_type: str
    price_kind: str  # fixed | floor | ceiling
    booking_from: date
    booking_to: date | None
    source_url: str
    fetched_at: datetime

    def applies_to(self, booking: date) -> bool:
        return self.booking_from <= booking and (
            self.booking_to is None or booking <= self.booking_to
        )


@dataclass(frozen=True)
class Rate:
    chart_id: str
    cabin: str
    season: str
    miles: int
    source_ref: str
    origin_zone: str | None = None
    dest_zone: str | None = None
    origin_match: frozenset[str] = frozenset()
    dest_match: frozenset[str] = frozenset()
    flight_numbers: tuple[str, ...] = ()
    route_label: str | None = None


@dataclass(frozen=True)
class SeasonRange:
    group: str
    season: str
    date_from: date
    date_to: date


@dataclass
class Family:
    name: str
    charts: dict[str, Chart] = field(default_factory=dict)
    zone_groups: dict[str, str | None] = field(default_factory=dict)  # zone -> season group
    members: dict[tuple[str, str], str] = field(default_factory=dict)  # (kind, value) -> zone
    seasons: list[SeasonRange] = field(default_factory=list)
    rates: list[Rate] = field(default_factory=list)


def _csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():  # route-based families (JAL) have no zone files
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _split(value: str) -> list[str]:
    return [v for v in (value or "").split("|") if v]


def load_family(family_dir: Path) -> Family:
    m: dict[str, Any] = yaml.safe_load((family_dir / "manifest.yaml").read_text(encoding="utf-8"))
    fam = Family(m["chart_family"])
    for c in m["charts"]:
        fam.charts[c["chart_id"]] = Chart(
            chart_id=c["chart_id"],
            family=fam.name,
            airline=m["airline"],
            trip_type=c["trip_type"],
            price_kind=c.get("price_kind", m.get("price_kind")),
            booking_from=date.fromisoformat(str(c["booking_from"])),
            booking_to=date.fromisoformat(str(c["booking_to"])) if c.get("booking_to") else None,
            source_url=m["source_url"],
            fetched_at=datetime.fromisoformat(m["fetched_at"]),
        )
    for z in _csv(family_dir / "zones.csv"):
        fam.zone_groups[z["zone_code"]] = z["season_group"] or None
    for zm in _csv(family_dir / "zone_members.csv"):
        fam.members[(zm["match_kind"], zm["match_value"])] = zm["zone_code"]
    for s in _csv(family_dir / "seasons.csv"):
        fam.seasons.append(
            SeasonRange(
                s["season_group"],
                s["season"],
                date.fromisoformat(s["date_from"]),
                date.fromisoformat(s["date_to"]),
            )
        )
    fam.seasons.sort(key=lambda r: (r.group, r.date_from))
    for r in _csv(family_dir / "rates.csv"):
        if r["chart_id"] not in fam.charts:
            continue
        fam.rates.append(
            Rate(
                chart_id=r["chart_id"],
                cabin=r["cabin"],
                season=r["season"],
                miles=int(r["miles"]),
                source_ref=r["source_ref"],
                origin_zone=r.get("origin_zone") or None,
                dest_zone=r.get("dest_zone") or None,
                origin_match=frozenset(_split(r.get("origin_match", ""))),
                dest_match=frozenset(_split(r.get("dest_match", ""))),
                flight_numbers=tuple(_split(r.get("flight_numbers", ""))),
                route_label=r.get("route_label") or None,
            )
        )
    _check(fam)
    return fam


def _check(fam: Family) -> None:
    """Invariants the database used to enforce with constraints."""
    for r in fam.rates:
        assert r.cabin in ("Y", "PY", "C", "F") and r.season in SEASON_ORDER and r.miles > 0, r
        zoned = r.origin_zone is not None and r.dest_zone is not None
        routed = bool(r.origin_match) and bool(r.dest_match)
        assert zoned != routed, f"rate must be zone pair xor route: {r}"
    by_group: dict[str, list[SeasonRange]] = {}
    for s in fam.seasons:
        assert s.date_to >= s.date_from, s
        by_group.setdefault(s.group, []).append(s)
    for group, ranges in by_group.items():
        for a, b in itertools.pairwise(ranges):
            assert a.date_to < b.date_from, f"overlapping season ranges in {fam.name}/{group}"


class FareStore:
    def __init__(self, families: dict[str, Family]) -> None:
        self.families = families

    @classmethod
    def load(cls, charts_dir: Path | None = None) -> FareStore:
        charts_dir = charts_dir or data_dir() / "charts"
        fams = [
            load_family(d) for d in sorted(charts_dir.iterdir()) if (d / "manifest.yaml").exists()
        ]
        return cls({f.name: f for f in fams})

    def family(self, name: str) -> Family:
        return self.families[name]

    def zone_for(self, family: str, airports: list[str], city: str, country: str) -> str | None:
        """Most specific match wins: airport, then city, then country."""
        members = self.families[family].members
        for kind, values in (("airport", airports), ("city", [city]), ("country", [country])):
            for v in values:
                if (kind, v) in members:
                    return members[(kind, v)]
        return None

    def season_group(self, family: str, zone: str) -> str | None:
        return self.families[family].zone_groups.get(zone)

    def season_on(self, family: str, group: str, day: date) -> str | None:
        for s in self.families[family].seasons:
            if s.group == group and s.date_from <= day <= s.date_to:
                return s.season
        return None

    def season_segments(
        self, family: str, group: str, date_from: date, date_to: date
    ) -> list[dict[str, Any]]:
        """Seasons covering [date_from, date_to], clipped to it. Gaps mean no calendar."""
        spans: list[tuple[date, date, str]] = []
        for s in self.families[family].seasons:
            if s.group != group or s.date_to < date_from or s.date_from > date_to:
                continue
            start, end = max(s.date_from, date_from), min(s.date_to, date_to)
            # Calendars split at year ends (12/31 H + 1/1 H): merge adjacent same-season spans.
            if spans and spans[-1][2] == s.season and (start - spans[-1][1]).days == 1:
                spans[-1] = (spans[-1][0], end, s.season)
            else:
                spans.append((start, end, s.season))
        return [
            {"date_from": a.isoformat(), "date_to": b.isoformat(), "season": season}
            for a, b, season in spans
        ]

    def charts_for(self, family: str, booking: date, trip_type: str | None = None) -> list[Chart]:
        return [
            c
            for c in self.families[family].charts.values()
            if c.applies_to(booking) and (trip_type is None or c.trip_type == trip_type)
        ]


@cache
def get_fare_store() -> FareStore:
    return FareStore.load()
