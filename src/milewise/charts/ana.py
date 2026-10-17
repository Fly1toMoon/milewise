"""Parser for ANA's international award chart page (own-metal awards).

Source: https://www.ana.co.jp/ja/jp/guide/amc/award/international/terms/

The page is server-rendered HTML with three kinds of tables, all without captions,
so each table is classified by its header row and labelled from the text before it:

* zone table      header "ゾーン区分"-style rows: "<name>（Zone N）" | city list
* season tables   header "年度": one per season group, labelled "日本、…（ZoneX）…発着"
* rate tables     header "対象クラス": labelled "<A>（Zone a）発着 <B>（Zone b）";
                  the round-trip block comes after "往復のマイル数を表します",
                  the one-way block after "片道のマイル数を表します"

Rate cells are either a number, "設定なし" (cabin not offered), or
"改定前：X 改定後：Y" (we keep Y: the chart for bookings from 2025-06-24).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from itertools import pairwise

from selectolax.lexbor import LexborHTMLParser

PARSER_VERSION = "ana-intl-1"
CHART_FAMILY = "NH-INTL-OWN"
SOURCE_URL = "https://www.ana.co.jp/ja/jp/guide/amc/award/international/terms/"
BOOKING_FROM = date(2025, 6, 24)  # "2025年6月24日（火）0:00（日本時間）以降にご予約・発券"

EXPECTED_RATE_TABLES = {"RT": 25, "OW": 25}
_CABIN_RE = re.compile(r"[（(](PY|Y|C|F)[）)]")
SEASONS = ["L", "R", "H"]

_ZONE_RE = re.compile(r"Zone\s*(\d+)")
_RANGE_RE = re.compile(r"(\d{1,2})/(\d{1,2})\s*[～〜~]\s*(\d{1,2})/(\d{1,2})")
_NUM_RE = re.compile(r"\d{1,3}(?:,\d{3})+|\d+")


class ChartParseError(ValueError):
    pass


@dataclass(frozen=True)
class Zone:
    code: str
    name_ja: str
    cities_ja: tuple[str, ...]


@dataclass(frozen=True)
class SeasonRange:
    season_group: str
    season: str
    date_from: date
    date_to: date


@dataclass(frozen=True)
class Rate:
    trip_type: str  # "RT" | "OW"
    zone_a: str
    zone_b: str
    cabin: str
    season: str
    miles: int
    revised: bool  # True when the cell showed 改定前/改定後
    source_ref: str


@dataclass
class ParsedChart:
    zones: list[Zone] = field(default_factory=list)
    season_groups: dict[str, list[str]] = field(default_factory=dict)  # group -> zone codes
    seasons: list[SeasonRange] = field(default_factory=list)
    rates: list[Rate] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _text(html: str) -> str:
    html = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
    html = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"(&nbsp;|\s)+", " ", html).strip()


def _cell_text(node: object) -> str:
    return re.sub(r"\s+", " ", node.text(separator=" ")).strip()  # type: ignore[attr-defined]


def _rows(table_html: str) -> list[list[str]]:
    tree = LexborHTMLParser(table_html)
    return [[_cell_text(c) for c in tr.css("th, td")] for tr in tree.css("tr")]


def _parse_rate_cell(text: str) -> tuple[int | None, bool]:
    if "設定なし" in text:
        return None, False
    if "改定後" in text:
        after = text.split("改定後", 1)[1]
        return int(_NUM_RE.search(after).group().replace(",", "")), True  # type: ignore[union-attr]
    m = _NUM_RE.search(text)
    if not m:
        raise ChartParseError(f"unparseable rate cell: {text!r}")
    return int(m.group().replace(",", "")), False


def _cabin(label: str) -> str:
    # Match the code in parentheses: "プレミアム エコノミー（PY）" also contains "エコノミー".
    m = _CABIN_RE.search(label)
    if not m:
        raise ChartParseError(f"unknown cabin label: {label!r}")
    return m.group(1)


def _check_header(row: list[str], first: str) -> None:
    want = [first, "ローシーズン (L)", "レギュラーシーズン (R)", "ハイシーズン (H)"]
    if [c.replace("（", "(").replace("）", ")") for c in row] != want:
        raise ChartParseError(f"unexpected header {row!r}")


def parse_ana_award_chart(html: str) -> ParsedChart:
    out = ParsedChart()
    trip_type: str | None = None
    prev_end = 0
    rate_counts = {"RT": 0, "OW": 0}

    for idx, m in enumerate(re.finditer(r"<table.*?</table>", html, flags=re.S)):
        before = _text(html[prev_end : m.start()])
        prev_end = m.end()
        if "往復のマイル数を表します" in before:
            trip_type = "RT"
        if "片道のマイル数を表します" in before:
            trip_type = "OW"
        rows = [r for r in _rows(m.group()) if r]
        if not rows:
            continue
        header = rows[0]

        if header[0] == "年度":
            _check_header(header, "年度")
            label = before[-120:]
            zones = _ZONE_RE.findall(label)
            if not zones or "日本" not in label:
                raise ChartParseError(f"table {idx}: cannot read season group label {label!r}")
            group = "Z" + "-".join(zones)
            out.season_groups[group] = ["1", *zones]
            for row in rows[1:]:
                year = int(re.match(r"(\d{4})", row[0]).group(1))  # type: ignore[union-attr]
                for season, cell in zip(SEASONS, row[1:], strict=True):
                    for mo1, d1, mo2, d2 in _RANGE_RE.findall(cell):
                        start = date(year, int(mo1), int(d1))
                        end = date(year, int(mo2), int(d2))
                        if end < start:
                            raise ChartParseError(f"table {idx}: reversed range {cell!r}")
                        out.seasons.append(SeasonRange(group, season, start, end))

        elif header[0] == "対象クラス":
            _check_header(header, "対象クラス")
            if trip_type is None:
                raise ChartParseError(f"table {idx}: rate table before RT/OW marker")
            pair = _ZONE_RE.findall(before[-80:])
            if len(pair) < 2:
                raise ChartParseError(f"table {idx}: cannot read zone pair from {before[-80:]!r}")
            zone_a, zone_b = pair[-2], pair[-1]
            rate_counts[trip_type] += 1
            for row in rows[1:]:
                cabin = _cabin(row[0])
                for season, cell in zip(SEASONS, row[1:], strict=True):
                    miles, revised = _parse_rate_cell(cell)
                    if miles is None:
                        continue
                    ref = f"table#{idx} {trip_type} Zone{zone_a}-Zone{zone_b} {cabin}/{season}"
                    out.rates.append(
                        Rate(trip_type, zone_a, zone_b, cabin, season, miles, revised, ref)
                    )

        elif any(_ZONE_RE.search(r[0]) for r in rows):
            for row in rows:
                zm = _ZONE_RE.search(row[0])
                if not zm or len(row) < 2:
                    continue
                name = re.sub(r"[（(].*$", "", row[0]).strip()
                cities = tuple(c for c in re.split(r"\s+", row[1]) if c)
                out.zones.append(Zone(zm.group(1), name, cities))

    _validate(out, rate_counts)
    return out


def _validate(chart: ParsedChart, rate_counts: dict[str, int]) -> None:
    if rate_counts != EXPECTED_RATE_TABLES:
        raise ChartParseError(f"rate table counts {rate_counts} != {EXPECTED_RATE_TABLES}")
    if not chart.zones or not chart.season_groups:
        raise ChartParseError("zone or season tables missing")

    zone_codes = {z.code for z in chart.zones}
    grouped = {z for zs in chart.season_groups.values() for z in zs}
    if zone_codes != grouped:
        raise ChartParseError(f"zones without season group: {sorted(zone_codes ^ grouped)}")

    for group in chart.season_groups:
        ranges = sorted(
            (s for s in chart.seasons if s.season_group == group), key=lambda s: s.date_from
        )
        for a, b in pairwise(ranges):
            if b.date_from <= a.date_to:
                raise ChartParseError(f"{group}: overlapping {a} and {b}")
            if (b.date_from - a.date_to).days != 1:
                chart.warnings.append(f"{group}: gap between {a.date_to} and {b.date_from}")

    rt = {
        (r.zone_a, r.zone_b, r.cabin, r.season): r.miles for r in chart.rates if r.trip_type == "RT"
    }
    for r in chart.rates:
        if r.trip_type == "OW":
            full = rt.get((r.zone_a, r.zone_b, r.cabin, r.season))
            if full is None:
                chart.warnings.append(f"OW without RT: {r.source_ref}")
            elif full != 2 * r.miles:
                chart.warnings.append(f"OW != RT/2: {r.source_ref} OW={r.miles} RT={full}")
