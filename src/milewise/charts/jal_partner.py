"""Parser for JAL's distance-based partner award charts (Japan-resident versions).

  ワンワールド特典航空券     .../partner_air/oneworld/miles.html     one table, Y / C / F columns
  JMB提携航空会社特典航空券  .../partner_air/p_jmb/jmb_mile.html     one table per class, Y / PY / C / F

Both are priced by 総旅程距離: the sum of the actual segment miles of the whole itinerary
(one award covers the itinerary, round trip or one way), in bands such as 4,001 - 8,000.
"""

from __future__ import annotations

import hashlib
import html as htmllib
import itertools
import re
from dataclasses import dataclass, field

PARSER_VERSION = "jal-partner-1"
PROGRAMS = {
    "oneworld": {
        "family": "JL-INTL-OW",
        "name_ja": "ワンワールド特典航空券",
        "url": "https://www.jal.co.jp/jp/ja/jalmile/use/partner_air/oneworld/miles.html",
    },
    "jmb_partner": {
        "family": "JL-INTL-JMBP",
        "name_ja": "JMB提携航空会社特典航空券",
        "url": "https://www.jal.co.jp/jp/ja/jalmile/use/partner_air/p_jmb/jmb_mile.html",
    },
}
CLASS_WORDS = {
    "エコノミー": "Y",
    "プレミアムエコノミー": "PY",
    "プレミアム エコノミー": "PY",
    "ビジネス": "C",
    "ファースト": "F",
}
_BAND = re.compile(r"([\d,]+)\s*-\s*([\d,]+)")


def chart_id(program: str) -> str:
    return f"{PROGRAMS[program]['family']}-ITIN@2026-10-17"


def _text(fragment: str) -> str:
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", htmllib.unescape(fragment)).strip()


def _int(s: str) -> int:
    return int(s.replace(",", ""))


def _cabin(label: str) -> str:
    label = re.sub(r"[*＊]\d*|\s*クラス", "", label).strip()
    for word in sorted(CLASS_WORDS, key=len, reverse=True):
        if label.startswith(word):
            return CLASS_WORDS[word]
    raise ValueError(f"unknown class label {label!r}")


@dataclass
class ParseResult:
    rates: list[dict[str, str]] = field(default_factory=list)
    source_sha256: str = ""


def parse(program: str, page_html: str) -> ParseResult:
    res = ParseResult(source_sha256=hashlib.sha256(page_html.encode()).hexdigest())
    body = re.sub(r"<(script|style)\b.*?</\1>", " ", page_html, flags=re.S | re.I)
    parts = re.split(r"(<table\b.*?</table>)", body, flags=re.S | re.I)
    preceding, ti = "", 0
    for part in parts:
        if not part.lower().startswith("<table"):
            preceding = _text(part) or preceding
            continue
        rows = [
            [_text(c) for c in re.findall(r"<t[hd]\b.*?</t[hd]>", tr, flags=re.S | re.I)]
            for tr in re.findall(r"<tr\b.*?</tr>", part, flags=re.S | re.I)
        ]
        header = rows[0]
        dist_col = next(i for i, h in enumerate(header) if "総旅程距離" in h)
        if len(header) - dist_col > 2:  # one table, a column per class (oneworld)
            cabins = [_cabin(h) for h in header[dist_col + 1 :]]
        else:  # a table per class (JMB partners): the class is named just before it
            m = re.findall(
                r"(エコノミー|プレミアム ?エコノミー|ビジネス|ファースト)\s*クラス", preceding
            )
            cabins = [_cabin(m[-1]) if m and ti else "Y"]
        for r in rows[1:]:
            band = _BAND.search(r[dist_col])
            if not band:
                raise ValueError(f"table#{ti}: no distance band in {r}")
            lo, hi = _int(band.group(1)), _int(band.group(2))
            for cabin, cell in zip(cabins, r[dist_col + 1 :], strict=True):
                res.rates.append(
                    {
                        "chart_id": chart_id(program),
                        "distance_min": str(lo),
                        "distance_max": str(hi),
                        "cabin": cabin,
                        "season": "ANY",
                        "miles": str(_int(cell)),
                        "source_ref": f"table#{ti} {lo}-{hi} {cabin}",
                    }
                )
        ti += 1
    _check(res)
    return res


def _check(res: ParseResult) -> None:
    by_cabin: dict[str, list[tuple[int, int, int]]] = {}
    for r in res.rates:
        by_cabin.setdefault(r["cabin"], []).append(
            (int(r["distance_min"]), int(r["distance_max"]), int(r["miles"]))
        )
    for cabin, bands in by_cabin.items():
        bands.sort()
        if bands[0][0] != 1:
            raise ValueError(f"{cabin}: first band does not start at 1 mile")
        for (_lo1, hi1, m1), (lo2, _, m2) in itertools.pairwise(bands):
            if lo2 != hi1 + 1:
                raise ValueError(f"{cabin}: gap or overlap between {hi1} and {lo2}")
            if m2 < m1:
                raise ValueError(f"{cabin}: miles decrease with distance at {lo2}")
    order = [c for c in ("Y", "PY", "C", "F") if c in by_cabin]
    for a, b in itertools.pairwise(order):
        for (lo, _, ma), (_, _, mb) in zip(by_cabin[a], by_cabin[b], strict=True):
            if mb < ma:
                raise ValueError(f"{b} cheaper than {a} in band starting {lo}")
