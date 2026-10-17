"""Parser behaviour on a small synthetic page that mirrors the real structure.

The real page is not committed (design.md §5.4); this fixture only reproduces its
table layout with made-up numbers.
"""

from __future__ import annotations

import pytest

from milewise.charts import ana
from milewise.charts.ana import ChartParseError, parse_ana_award_chart

ZONES = """
<table><tr><td>日本（Zone 1）</td><td>日本</td></tr>
<tr><td>北米（Zone 6）</td><td>ロサンゼルス</td></tr>
<tr><td>欧州・ロシア2（Zone 7）</td><td><ul><li>ロンドン</li><li>パリ</li></ul></td></tr></table>
"""
SEASONS = """
<p>日本、北米（Zone6）または欧州・ロシア2（Zone7）発着</p>
<table><tr><th>年度</th><th>ローシーズン (L)</th><th>レギュラーシーズン (R)</th><th>ハイシーズン (H)</th></tr>
<tr><th>2026年</th><td><ul><li>1/1～1/31</li></ul></td><td>2/1～2/28<br>3/1～3/31</td><td>4/1～12/31</td></tr></table>
"""


def rate_table(a: str, b: str, h_cell: str = "改定前：100<br>改定後：140") -> str:
    return f"""
<p>{a}発着 {b}</p>
<table><tr><th>対象クラス</th><th>ローシーズン (L)</th><th>レギュラーシーズン (R)</th><th>ハイシーズン (H)</th></tr>
<tr><th>エコノミー（Y）</th><td><p>80</p></td><td>90</td><td>{h_cell}</td></tr>
<tr><th>プレミアム<br>エコノミー（PY）</th><td>設定なし</td><td>設定なし</td><td>設定なし</td></tr>
<tr><th>ビジネスクラス（C）</th><td>200</td><td>220</td><td>300</td></tr></table>
"""


def page(rt_h: str = "改定前：100<br>改定後：140", ow_h: str = "70") -> str:
    return (
        ZONES
        + SEASONS
        + "<p>必要マイルチャートの数字は、往復のマイル数を表します。</p>"
        + rate_table("日本（Zone 1）", "欧州・ロシア2（Zone 7）", rt_h)
        + "<p>必要マイルチャートの数字は、片道のマイル数を表します。</p>"
        + rate_table("日本（Zone 1）", "欧州・ロシア2（Zone 7）", ow_h)
        .replace(">80<", ">40<")
        .replace(">90<", ">45<")
        .replace(">200<", ">100<")
        .replace(">220<", ">110<")
        .replace(">300<", ">150<")
    )


@pytest.fixture(autouse=True)
def one_table_each(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ana, "EXPECTED_RATE_TABLES", {"RT": 1, "OW": 1})


def test_parses_zones_seasons_and_rates() -> None:
    c = parse_ana_award_chart(page())
    assert [(z.code, z.cities_ja) for z in c.zones] == [
        ("1", ("日本",)),
        ("6", ("ロサンゼルス",)),
        ("7", ("ロンドン", "パリ")),
    ]
    assert c.season_groups == {"Z6-7": ["1", "6", "7"]}
    assert len(c.seasons) == 4
    rt = {(r.cabin, r.season): r.miles for r in c.rates if r.trip_type == "RT"}
    assert rt[("Y", "H")] == 140  # 改定後, not 改定前
    assert ("PY", "L") not in rt  # 設定なし is dropped, and PY is not mistaken for Y
    assert rt[("Y", "L")] == 80
    assert c.warnings == []


def test_one_way_must_be_half_of_round_trip() -> None:
    c = parse_ana_award_chart(page(ow_h="71"))
    assert any("OW != RT/2" in w for w in c.warnings)


def test_zone_without_season_calendar_fails_loudly() -> None:
    with pytest.raises(ChartParseError, match="zones without season group"):
        parse_ana_award_chart(
            page().replace("<tr><td>北米（Zone 6）</td><td>ロサンゼルス</td></tr>", "")
        )


def test_missing_rate_tables_fail_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ana, "EXPECTED_RATE_TABLES", {"RT": 25, "OW": 25})
    with pytest.raises(ChartParseError, match="rate table counts"):
        parse_ana_award_chart(page())
