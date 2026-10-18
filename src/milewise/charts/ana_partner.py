"""Parser for ANA's partner-airline award chart (提携航空会社特典航空券 ゾーン・必要マイルチャート).

https://www.ana.co.jp/ja/jp/guide/amc/award/tk/zone/  — differs from the own-metal chart:
  * a round-trip chart and, for bookings from 2025-06-24, a one-way chart (the page's
    "片道必要マイルチャート"; the partner usage page still says 片道ではご利用になれません,
    design.md §9.15); no seasons; classes Y / C / F (no PY);
  * Japan is split into Zone 1-A (an itinerary of only two international segments, plus
    connections within Japan) and Zone 1-B (used when 1-A has no price);
  * zones are lists of countries, and every zone pair has its own small table, including
    pairs that do not touch Japan (e.g. アジア1 ⇔ 欧州).
Where a cell shows 改定前／改定後, only 改定後 (bookings from 2024-04-18) is kept.

The page prints each pair in both directions (A⇔B and B⇔A); the parser checks that the
copies agree instead of trusting the first one.
"""

from __future__ import annotations

import hashlib
import html as htmllib
import re
from dataclasses import dataclass, field

PARSER_VERSION = "ana-partner-1"
FAMILY = "NH-INTL-PARTNER"
CHART_IDS = {"RT": f"{FAMILY}-RT@2024-04-18", "OW": f"{FAMILY}-OW@2025-06-24"}
BOOKING_FROM = {"RT": "2024-04-18", "OW": "2025-06-24"}
SOURCE_URL = "https://www.ana.co.jp/ja/jp/guide/amc/award/tk/zone/"
CABINS = {"エコノミークラス": "Y", "ビジネスクラス": "C", "ファーストクラス": "F"}

# Zone names as printed in the table labels -> zone code.
ZONE_NAMES = {
    "日本（Zone 1-A）": "1A",
    "日本（Zone 1-B）": "1B",
    "韓国・ロシア1（Zone 2）": "2",
    "アジア1（Zone 3）": "3",
    "アジア2・ロシア3（Zone 4）": "4",
    "ハワイ（Zone 5）": "5",
    "北米（Zone 6）": "6",
    "欧州・ロシア2（Zone 7）": "7",
    "アフリカ、中東（Zone 8）": "8",
    "中南米（Zone 9）": "9",
    "オセアニア、ミクロネシア（Zone 10）": "10",
}
ZONE_LABELS = {
    "1A": "日本（Zone 1-A）",
    "1B": "日本（Zone 1-B）",
    **{v: k for k, v in ZONE_NAMES.items() if v not in ("1A", "1B")},
}

# Country names in the zone table -> ISO 3166-1 alpha-2. Names that are not a whole
# country (ロシア沿海地方, ハワイ, アラスカ, island groups...) are handled as city overrides
# or left unmapped (reported in the manifest).
JA_COUNTRY = {
    "日本": "JP",
    "韓国": "KR",
    "グアム": "GU",
    "台湾": "TW",
    "中国大陸": "CN",
    "フィリピン": "PH",
    "香港": "HK",
    "マカオ": "MO",
    "アフガニスタン": "AF",
    "インド": "IN",
    "インドネシア": "ID",
    "ウズベキスタン": "UZ",
    "カザフスタン": "KZ",
    "カンボジア": "KH",
    "キルギス": "KG",
    "シンガポール": "SG",
    "スリランカ": "LK",
    "タイ": "TH",
    "タジキスタン": "TJ",
    "トルクメニスタン": "TM",
    "ネパール": "NP",
    "パキスタン": "PK",
    "バングラディッシュ": "BD",
    "東ティモール": "TL",
    "ブータン": "BT",
    "ブルネイ": "BN",
    "ベトナム": "VN",
    "マレーシア": "MY",
    "ミャンマー": "MM",
    "モルディブ": "MV",
    "モンゴル": "MN",
    "ラオス": "LA",
    "アメリカ合衆国": "US",
    "カナダ": "CA",
    "メキシコ": "MX",
    "アイスランド": "IS",
    "アイルランド": "IE",
    "アゼルバイジャン": "AZ",
    "アルジェリア": "DZ",
    "アルバニア": "AL",
    "アルメニア": "AM",
    "アンドラ": "AD",
    "イギリス": "GB",
    "イタリア": "IT",
    "ウクライナ": "UA",
    "エストニア": "EE",
    "オーストリア": "AT",
    "オランダ": "NL",
    "キプロス": "CY",
    "ギリシャ": "GR",
    "クロアチア": "HR",
    "サンマリノ": "SM",
    "ジョージア": "GE",
    "スイス": "CH",
    "スウェーデン": "SE",
    "スペイン": "ES",
    "スロバキア": "SK",
    "スロベニア": "SI",
    "セルビア": "RS",
    "チェコ共和国": "CZ",
    "チュニジア": "TN",
    "デンマーク": "DK",
    "ドイツ": "DE",
    "トルコ": "TR",
    "ノルウェー": "NO",
    "バチカン市国": "VA",
    "ハンガリー": "HU",
    "フィンランド": "FI",
    "フランス": "FR",
    "ブルガリア": "BG",
    "ベラルーシ": "BY",
    "ベルギー": "BE",
    "ポーランド": "PL",
    "ボスニアヘルツェゴビナ": "BA",
    "ポルトガル": "PT",
    "北マケドニア": "MK",
    "マルタ": "MT",
    "モナコ": "MC",
    "モルドバ共和国": "MD",
    "モンテネグロ": "ME",
    "モロッコ": "MA",
    "ラトビア": "LV",
    "リトアニア": "LT",
    "リヒテンシュタイン": "LI",
    "ルーマニア": "RO",
    "ルクセンブルグ": "LU",
    "アラブ首長国連邦": "AE",
    "アンゴラ": "AO",
    "イエメン": "YE",
    "イスラエル": "IL",
    "イラク": "IQ",
    "イラン": "IR",
    "ウガンダ": "UG",
    "エジプト": "EG",
    "エチオピア": "ET",
    "オマーン": "OM",
    "ガーナ": "GH",
    "カタール": "QA",
    "クウェート": "KW",
    "ケニア": "KE",
    "サウジアラビア": "SA",
    "ヨルダン": "JO",
    "レバノン": "LB",
    "バーレーン": "BH",
    "南アフリカ": "ZA",
    "ナイジェリア": "NG",
    "タンザニア": "TZ",
    "モーリシャス": "MU",
    "セイシェル": "SC",
    "マダガスカル": "MG",
    "アルゼンチン": "AR",
    "ブラジル": "BR",
    "チリ": "CL",
    "ペルー": "PE",
    "コロンビア": "CO",
    "キューバ": "CU",
    "パナマ": "PA",
    "コスタリカ": "CR",
    "グアテマラ": "GT",
    "エクアドル": "EC",
    "ウルグアイ": "UY",
    "パラグアイ": "PY",
    "ボリビア": "BO",
    "ベネズエラ": "VE",
    "ジャマイカ": "JM",
    "ドミニカ共和国": "DO",
    "プエルトリコ": "PR",
    "バハマ": "BS",
    "オーストラリア": "AU",
    "ニュージーランド": "NZ",
    "フィジー": "FJ",
    "パラオ": "PW",
    "パプアニューギニア": "PG",
    "ニューカレドニア": "NC",
    "フランス領ポリネシア": "PF",
    "サモア": "WS",
    "トンガ": "TO",
    "ヴァヌアトゥ": "VU",
    "ソロモン諸島": "SB",
    "マーシャル諸島": "MH",
    "キリバス": "KI",
    "ナウル": "NR",
    "クック諸島": "CK",
}
# Countries split across zones are matched per city instead (city codes of cities.csv).
CITY_OVERRIDES = {"VVO": "2", "MOW": "7", "HNL": "5", "KOA": "5"}
# Region words a user may use instead of a city ("中国到欧洲"): data/locations/areas.csv.
REGION_ZONES = {
    "EUROPE": "7",
    "NORTH_AMERICA": "6",
    "MIDDLE_EAST": "8",
    "AFRICA": "8",
    "LATIN_AMERICA": "9",
    "OCEANIA": "10",
}

_NUM = re.compile(r"\d{1,3}(?:,\d{3})+|\d+")


def _text(fragment: str) -> str:
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", htmllib.unescape(fragment)).strip()


def _norm_zone(label: str) -> str:
    return re.sub(r"\s+", "", label)


_RANK = {"1A": 0, "1B": 1, **{str(i): i for i in range(2, 11)}}


def _canon(a: str, b: str) -> tuple[str, str]:
    """Pairs are symmetric (⇔); store each once, Japan / lower zone first."""
    return (a, b) if _RANK[a] <= _RANK[b] else (b, a)


@dataclass
class ParseResult:
    zones: list[dict[str, str]] = field(default_factory=list)
    members: list[dict[str, str]] = field(default_factory=list)
    rates: list[dict[str, str]] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source_sha256: str = ""
    n_tables: int = 0


def _miles(cell: str) -> int | None:
    """改定前：65,000 改定後*：85,000 -> 85000; 設定なし -> None."""
    if "設定なし" in cell:
        return None
    if "改定後" in cell:
        cell = cell.split("改定後", 1)[1]
    nums = _NUM.findall(cell)
    if not nums:
        raise ValueError(f"no figure in cell {cell!r}")
    return int(nums[0].replace(",", ""))


def parse(page_html: str) -> ParseResult:
    res = ParseResult(source_sha256=hashlib.sha256(page_html.encode()).hexdigest())
    body = re.sub(r"<(script|style)\b.*?</\1>", " ", page_html, flags=re.S | re.I)
    parts = re.split(r"(<table\b.*?</table>)", body, flags=re.S | re.I)
    zone_codes = {_norm_zone(k): v for k, v in ZONE_NAMES.items()}
    seen: dict[tuple[str, str, str, str], tuple[int | None, str]] = {}
    preceding, block = "", "RT"
    for part in parts:
        if not part.lower().startswith("<table"):
            preceding = _text(part) or preceding
            if "片道のマイル数を表します" in preceding:
                block = "OW"
            continue
        res.n_tables += 1
        rows = [
            [_text(c) for c in re.findall(r"<t[hd]\b.*?</t[hd]>", tr, flags=re.S | re.I)]
            for tr in re.findall(r"<tr\b.*?</tr>", part, flags=re.S | re.I)
        ]
        if rows and rows[0][:2] == ["発着地", "対象地域"]:
            _zone_table(rows[1:], res)
            continue
        if not rows or rows[0][:2] != ["利用クラス", "必要マイル数"]:
            res.warnings.append(f"table#{res.n_tables - 1}: unexpected header {rows[:1]}")
            continue
        labels = re.findall(r"([^。*]+?)⇔([^。*]+?)の場合", preceding)
        if not labels:
            raise ValueError(f"table#{res.n_tables - 1}: no zone-pair label before it")
        a, b = _canon(*_pair_codes(labels[-1], zone_codes))
        for cells in rows[1:]:
            cabin = next((code for name, code in CABINS.items() if cells[0].startswith(name)), None)
            if cabin is None:
                raise ValueError(f"unknown class row {cells}")
            miles = _miles(cells[1])
            key = (block, a, b, cabin)
            if key in seen and seen[key][0] != miles:
                raise ValueError(f"copies disagree for {key}: {seen[key][0]} vs {miles}")
            seen.setdefault(key, (miles, f"table#{res.n_tables - 1} {block} {a}-{b} {cabin}"))
    for (blk, a, b, cabin), (miles, ref) in sorted(seen.items(), key=lambda kv: _order(kv[0])):
        if miles is None:
            continue  # 設定なし: no row, the tool reports "not offered"
        res.rates.append(
            {
                "chart_id": CHART_IDS[blk],
                "origin_zone": a,
                "dest_zone": b,
                "cabin": cabin,
                "season": "ANY",
                "miles": str(miles),
                "source_ref": ref,
            }
        )
    _check(res, seen)
    return res


def _pair_codes(label: tuple[str, str], zone_codes: dict[str, str]) -> tuple[str, str]:
    out = []
    for side in label:
        norm = _norm_zone(side)
        # the label text may carry the previous sentence; take the zone name at its end
        code = next((c for name, c in zone_codes.items() if norm.endswith(name)), None)
        if code is None:
            raise ValueError(f"unknown zone in label {side!r}")
        out.append(code)
    return out[0], out[1]


def _order(key: tuple[str, str, str, str]) -> tuple[int, int, int, int]:
    return "RO".index(key[0][0]), _RANK[key[1]], _RANK[key[2]], "YCF".index(key[3])


def _zone_table(rows: list[list[str]], res: ParseResult) -> None:
    zone_codes = {_norm_zone(k): v for k, v in ZONE_NAMES.items()}
    for label, countries in rows:
        norm = _norm_zone(label)
        if norm.startswith("日本"):
            for code in ("1A", "1B"):
                res.zones.append(
                    {
                        "chart_family": FAMILY,
                        "zone_code": code,
                        "name_ja": ZONE_LABELS[code],
                        "season_group": "",
                    }
                )
            res.members.append(
                {
                    "chart_family": FAMILY,
                    "zone_code": "1A",
                    "match_kind": "country",
                    "match_value": "JP",
                }
            )
            continue
        found = next((c for name, c in zone_codes.items() if name == norm), None)
        if found is None:
            raise ValueError(f"unknown zone row {label!r}")
        code = found
        res.zones.append(
            {
                "chart_family": FAMILY,
                "zone_code": code,
                "name_ja": ZONE_LABELS[code],
                "season_group": "",
            }
        )
        for name in re.split(r"、", countries):
            base = re.sub(r"（.*?）", "", name).strip()
            iso = JA_COUNTRY.get(base)
            if iso and "ロシア" not in name:
                res.members.append(
                    {
                        "chart_family": FAMILY,
                        "zone_code": code,
                        "match_kind": "country",
                        "match_value": iso,
                    }
                )
            else:
                res.unmapped.append(f"Zone {code}: {name.strip()}")
    for city, code in CITY_OVERRIDES.items():
        res.members.append(
            {"chart_family": FAMILY, "zone_code": code, "match_kind": "city", "match_value": city}
        )
    for region, code in REGION_ZONES.items():
        res.members.append(
            {
                "chart_family": FAMILY,
                "zone_code": code,
                "match_kind": "region",
                "match_value": region,
            }
        )


def _check(res: ParseResult, seen: dict[tuple[str, str, str, str], tuple[int | None, str]]) -> None:
    codes = {z["zone_code"] for z in res.zones}
    if len(codes) != 11:
        raise ValueError(f"expected 11 zones (1-A, 1-B, 2-10), got {sorted(codes)}")
    zones = codes - {"1A", "1B"}
    expected = {_canon(j, z) for j in ("1A", "1B") for z in zones}
    expected |= {_canon(a, b) for a in zones for b in zones}
    for blk in ("RT", "OW"):
        missing = expected - {(a, b) for k, a, b, _ in seen if k == blk}
        if missing:
            raise ValueError(f"{blk}: zone pairs without a table: {sorted(missing)}")
    for (blk, a, b, cabin), (miles, _) in seen.items():
        if a == "1A" and miles is not None:
            b_price = seen.get((blk, "1B", b, cabin), (None, ""))[0]
            if b_price is not None and b_price < miles:
                res.warnings.append(f"1-B cheaper than 1-A for {b} {cabin}: {b_price} < {miles}")
        if blk == "OW" and miles is not None:
            # cross-check: a one-way award should cost about half the round trip
            rt = seen.get(("RT", a, b, cabin), (None, ""))[0]
            if rt is not None and not 0.4 * rt <= miles <= 0.6 * rt:
                res.warnings.append(f"OW {a}-{b} {cabin} {miles} is not ~half of RT {rt}")
