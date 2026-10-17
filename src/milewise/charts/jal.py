"""Parser for JAL's international award chart page (JAL-operated awards).

Source: https://www.jal.co.jp/jp/ja/jalmile/use/jal/inter/routemiles.html (site blocks
scripts; the page is saved from a browser, design.md §5.2).

The page is one-way, Japan-origin/destination only ("日本発着（片道）"), in three blocks,
each with one table per region (東アジア … 北米):

* 基本マイル数     Y / PY / C, no seasons. The lowest price: when those seats are gone,
                   特典航空券PLUS charges more, so these are floors.
* ファーストクラス L / R / H by the first-class season calendar (one calendar table).
* PLUS利用時       Y / PY / C maximum with PLUS ("PLUS利用時の最大のマイル数"): ceilings.

Rows are route labels, mapped by hand in ROUTES; an unmapped label fails the parse, so a
new route is noticed instead of guessed. Round trip = sum of the two one-way legs
("往復でご利用の場合は、片道の必要マイル数を合算", JAL 必要なマイル数 page).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from itertools import pairwise

from selectolax.lexbor import LexborHTMLParser

PARSER_VERSION = "jal-intl-1"
CHART_FAMILY = "JL-INTL-OWN"
SOURCE_URL = "https://www.jal.co.jp/jp/ja/jalmile/use/jal/inter/routemiles.html"
REGIONS = [
    "東アジア",
    "東南アジア・南アジア",
    "グアム",
    "ハワイ",
    "オセアニア",
    "中東",
    "ヨーロッパ",
    "北米",
]
JP = ["country:JP"]

# label -> (origin match, destination match, flight numbers or None)
ROUTES: dict[str, tuple[list[str], list[str], list[str] | None]] = {
    "北京": (JP, ["city:BJS"], None),
    "上海（虹橋）便": (JP, ["airport:SHA"], None),
    "上海（浦東）便": (JP, ["airport:PVG"], None),
    "広州": (JP, ["city:CAN"], None),
    "大連": (JP, ["city:DLC"], None),
    "天津": (JP, ["city:TSN"], None),
    "香港": (JP, ["city:HKG"], None),
    "台北－東京（羽田）便": (["airport:HND"], ["city:TPE"], None),
    "台北－東京（成田）、大阪（関西）、名古屋（中部）便": (
        ["airport:NRT", "airport:KIX", "airport:NGO"],
        ["city:TPE"],
        None,
    ),
    "台北－沖縄（那覇）": (["airport:OKA"], ["city:TPE"], None),
    "ソウル": (JP, ["city:SEL"], None),
    # The PLUS block groups some routes differently from the basic-miles block.
    "上海": (JP, ["city:SHA"], None),
    "台北－東京（羽田・成田）便": (["airport:HND", "airport:NRT"], ["city:TPE"], None),
    "台北－大阪（関西）、名古屋（中部）便": (["airport:KIX", "airport:NGO"], ["city:TPE"], None),
    "バンコク－東京（羽田・成田）便": (["airport:HND", "airport:NRT"], ["city:BKK"], None),
    "バンコク－東京（羽田・成田）便 ※JL031/JL034便以外": (
        ["airport:HND", "airport:NRT"],
        ["city:BKK"],
        None,
    ),
    "バンコク JL031/JL034便": (["airport:HND", "airport:NRT"], ["city:BKK"], ["JL031", "JL034"]),
    "バンコク－大阪（関西）、名古屋（中部）便": (
        ["airport:KIX", "airport:NGO"],
        ["city:BKK"],
        None,
    ),
    "シンガポール": (JP, ["city:SIN"], None),
    "クアラルンプール": (JP, ["city:KUL"], None),
    "ジャカルタ": (JP, ["city:JKT"], None),
    "ハノイ": (JP, ["city:HAN"], None),
    "ホーチミンシティ": (JP, ["city:SGN"], None),
    "マニラ": (JP, ["city:MNL"], None),
    "デリー": (JP, ["city:DEL"], None),
    "ベンガルール": (JP, ["city:BLR"], None),
    "グアム": (JP, ["city:GUM"], None),
    "ホノルル": (JP, ["city:HNL"], None),
    "コナ": (JP, ["city:KOA"], None),
    "シドニー": (JP, ["city:SYD"], None),
    "メルボルン": (JP, ["city:MEL"], None),
    "ドーハ": (JP, ["city:DOH"], None),
    "ロンドン": (JP, ["city:LON"], None),
    "パリ": (JP, ["city:PAR"], None),
    "フランクフルト": (JP, ["city:FRA"], None),
    "ヘルシンキ": (JP, ["city:HEL"], None),
    "ニューヨーク": (JP, ["city:NYC"], None),
    "ボストン": (JP, ["city:BOS"], None),
    "シカゴ": (JP, ["city:CHI"], None),
    "ダラス": (JP, ["city:DFW"], None),
    "サンフランシスコ": (JP, ["city:SFO"], None),
    "ロサンゼルス": (JP, ["city:LAX"], None),
    "サンディエゴ": (JP, ["city:SAN"], None),
    "シアトル": (JP, ["city:SEA"], None),
    "バンクーバー": (JP, ["city:YVR"], None),
}

CABIN_HEADERS = {
    "エコノミークラス": "Y",
    "プレミアム エコノミークラス": "PY",
    "ビジネスクラス": "C",
}
SEASON_HEADERS = {
    "ローシーズン（L）": "L",
    "レギュラーシーズン（R）": "R",
    "ハイシーズン（H）": "H",
}
_NUM = re.compile(r"\d{1,3}(?:,\d{3})+|\d+")
_DATE_RANGE = re.compile(
    r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日\s*[～〜~]\s*(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日"
)


class ChartParseError(ValueError):
    pass


@dataclass(frozen=True)
class RouteRate:
    block: str  # base | first | plus_max
    region: str
    label: str
    cabin: str
    season: str  # ANY for base / plus_max
    miles: int
    source_ref: str


@dataclass(frozen=True)
class SeasonRange:
    season: str
    date_from: date
    date_to: date


@dataclass
class ParsedJalChart:
    rates: list[RouteRate] = field(default_factory=list)
    seasons: list[SeasonRange] = field(default_factory=list)
    labels: dict[str, set[str]] = field(default_factory=dict)  # block -> labels seen
    warnings: list[str] = field(default_factory=list)  # data problems: block writing CSVs
    notes: list[str] = field(default_factory=list)  # facts of the page worth recording


def _text(html: str) -> str:
    html = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
    return re.sub(r"(&nbsp;|\s)+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _rows(table_html: str) -> list[list[str]]:
    tree = LexborHTMLParser(table_html)
    for n in tree.css("script, style"):
        n.decompose()
    return [
        [re.sub(r"\s+", " ", c.text(separator=" ")).strip() for c in tr.css("th, td")]
        for tr in tree.css("tr")
    ]


def _miles(cell: str) -> int | None:
    if cell in ("－", "-", "―", ""):
        return None
    m = _NUM.search(cell)
    if not m:
        raise ChartParseError(f"unparseable cell {cell!r}")
    return int(m.group().replace(",", ""))


def _parse_seasons(rows: list[list[str]]) -> list[SeasonRange]:
    out = []
    for row in rows:
        season = next((v for k, v in SEASON_HEADERS.items() if k in row[0]), None)
        if season is None:
            raise ChartParseError(f"unknown season row {row[0][-30:]!r}")
        year = 0
        for y1, m1, d1, y2, m2, d2 in _DATE_RANGE.findall(row[1]):
            year = int(y1) if y1 else year
            start = date(year, int(m1), int(d1))
            year = int(y2) if y2 else year
            end = date(year, int(m2), int(d2))
            if not year or end < start:
                raise ChartParseError(f"bad season range in {row[1][-60:]!r}")
            out.append(SeasonRange(season, start, end))
    return out


def parse_jal_award_chart(html: str) -> ParsedJalChart:
    out = ParsedJalChart(labels={"base": set(), "first": set(), "plus_max": set()})
    block = "base"
    prev_end = 0
    for idx, m in enumerate(re.finditer(r"<table.*?</table>", html, flags=re.S)):
        before = _text(html[prev_end : m.start()])
        prev_end = m.end()
        if "PLUS利用時の最大のマイル数" in before:
            block = "plus_max"
        rows = [r for r in _rows(m.group()) if r]
        header = rows[0]
        if any(k in header[0] for k in SEASON_HEADERS):  # first-class season calendar
            out.seasons = _parse_seasons(rows)
            block = "first"
            continue
        if header[0] != "路線":
            raise ChartParseError(f"table {idx}: unexpected header {header!r}")
        # The region heading is the last region name before the table (the text between
        # two tables can be shorter than any fixed window, so take the last occurrence).
        found = {r: before.rfind(r) for r in REGIONS if before.rfind(r) >= 0}
        region = max(found, key=lambda r: (found[r], len(r))) if found else None
        if region is None:
            raise ChartParseError(f"table {idx}: region label not found in {before[-60:]!r}")
        if block == "first":
            cols = [SEASON_HEADERS.get(h) for h in header[1:]]
        else:
            cols = [CABIN_HEADERS.get(h) for h in header[1:]]
        if None in cols:
            raise ChartParseError(f"table {idx}: unknown column in {header!r}")
        for row in rows[1:]:
            label = row[0]
            if label not in ROUTES:
                raise ChartParseError(
                    f"table {idx}: unmapped route label {label!r} (add it to ROUTES)"
                )
            out.labels[block].add(label)
            for col, cell in zip(cols, row[1:], strict=True):
                miles = _miles(cell)
                if miles is None:
                    continue
                cabin, season = ("F", col) if block == "first" else (col, "ANY")
                ref = f"table#{idx} {block} {region} {label} {cabin}/{season}"
                out.rates.append(
                    RouteRate(block, region, label, str(cabin), str(season), miles, ref)
                )
    _validate(out)
    return out


def _city(token: str) -> str:
    kind, value = token.split(":", 1)
    if kind == "city":
        return value
    from milewise.locations import load_cities

    return next(c.city_code for c in load_cities() if value in c.airports)


def overlaps(a_label: str, b_label: str) -> bool:
    """Do two route labels cover a common origin and destination?"""
    (ao, ad, _), (bo, bd, _) = ROUTES[a_label], ROUTES[b_label]
    origin = "country:JP" in ao or "country:JP" in bo or bool(set(ao) & set(bo))
    return origin and bool({_city(t) for t in ad} & {_city(t) for t in bd})


def _validate(chart: ParsedJalChart) -> None:
    if not chart.seasons:
        raise ChartParseError("first-class season calendar missing")
    for block in ("base", "first", "plus_max"):
        if not chart.labels[block]:
            raise ChartParseError(f"block {block} has no routes")
    ranges = sorted(chart.seasons, key=lambda s: s.date_from)
    for prev, nxt in pairwise(ranges):
        if nxt.date_from <= prev.date_to:
            raise ChartParseError(f"overlapping first-class seasons {prev} / {nxt}")
        if (nxt.date_from - prev.date_to).days != 1:
            chart.warnings.append(f"season gap between {prev.date_to} and {nxt.date_from}")

    # Cross-check the two Y/PY/C blocks by route coverage (they group routes differently):
    # every basic-miles route has a PLUS route covering it, and PLUS max >= basic miles.
    base = [r for r in chart.rates if r.block == "base"]
    plus = [r for r in chart.rates if r.block == "plus_max"]
    for row in base:
        covering = [p for p in plus if p.cabin == row.cabin and overlaps(row.label, p.label)]
        if not covering:  # e.g. a cabin PLUS is not sold for: a property of the page
            chart.notes.append(
                f"no PLUS maximum published for {row.region} {row.label} {row.cabin}"
            )
        for p in covering:
            if p.miles < row.miles:
                chart.warnings.append(
                    f"PLUS max below base: {p.source_ref}={p.miles} < {row.source_ref}={row.miles}"
                )
