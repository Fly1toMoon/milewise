"""Golden tests for partner awards, values read from the official pages (2026-10-17):

ANA  https://www.ana.co.jp/ja/jp/guide/amc/award/tk/zone/ (改定後, bookings from 2024-04-18;
     one-way chart for bookings from 2025-06-24)
JAL  https://www.jal.co.jp/jp/ja/jalmile/use/partner_air/oneworld/miles.html and
     https://www.jal.co.jp/jp/ja/jalmile/use/partner_air/p_jmb/jmb_mile.html
"""

from __future__ import annotations

from datetime import date

from milewise.charts.store import FareStore
from milewise.locations import resolve_area
from milewise.tools.miles import get_required_miles

BOOKING = date(2026, 10, 18)


def partner(fares: FareStore, airline: str, o: str, d: str, cabin: str, **kw: object):  # type: ignore[no-untyped-def]
    return get_required_miles(
        fares,
        airline,
        o,
        d,
        cabin,
        booking_date=BOOKING,
        award_type="partner",
        **kw,  # type: ignore[arg-type]
    )


def test_china_to_europe_by_country_and_region(fares: FareStore) -> None:
    rt = partner(fares, "NH", "中国", "欧洲", "C", trip_type="RT")
    assert rt.status == "ok" and rt.total_miles == 118_000  # Zone 3 ⇔ Zone 7
    ow = partner(fares, "NH", "中国", "欧洲", "C", trip_type="OW")
    assert ow.total_miles == 59_000
    assert ow.chart["chart_id"] == "NH-INTL-PARTNER-OW@2025-06-24"  # type: ignore[index]
    assert any("片道ではご利用になれません" in c for c in ow.caveats)  # the pages disagree


def test_city_pair_abroad_matches_country_pair(fares: FareStore) -> None:
    assert partner(fares, "NH", "上海", "パリ", "Y", trip_type="RT").total_miles == 60_000


def test_japan_returns_zone_1a_and_1b(fares: FareStore) -> None:
    res = partner(fares, "NH", "東京", "ロンドン", "C", trip_type="RT")
    assert [r["miles"] for r in res.results] == [115_000, 121_000]
    assert res.results[0]["variant"].startswith("Zone 1-A")


def test_zone_1a_not_offered_falls_back_to_1b(fares: FareStore) -> None:
    # 日本（Zone 1-A）⇔中南米（Zone 9）: 設定なし; Zone 1-B Y 115,000
    res = partner(fares, "NH", "東京", "中南米", "Y", trip_type="RT")
    assert res.total_miles == 115_000
    assert any("Zone 1-B が適用" in c for c in res.caveats)


def test_partner_has_no_premium_economy_and_no_domestic(fares: FareStore) -> None:
    assert partner(fares, "NH", "東京", "パリ", "PY", trip_type="RT").status == "not_found"
    assert partner(fares, "NH", "東京", "大阪", "Y", trip_type="RT").status == "not_found"


def test_hawaii_and_russia_use_city_zones(fares: FareStore) -> None:
    assert (
        partner(fares, "NH", "東京", "ホノルル", "Y", trip_type="RT").results[0]["miles"] == 40_000
    )
    assert (
        partner(fares, "NH", "東京", "ウラジオストク", "Y", trip_type="RT").results[0]["miles"]
        == 15_000
    )


def test_jal_partner_is_distance_based(fares: FareStore) -> None:
    res = partner(fares, "JL", "東京", "ロンドン", "C", total_distance_miles=11_960)
    assert [r["miles"] for r in res.results] == [110_000, 100_000]  # oneworld, JMB partners
    one = partner(
        fares,
        "JL",
        "東京",
        "ロンドン",
        "C",
        total_distance_miles=11_960,
        partner_program="oneworld",
    )
    assert one.total_miles == 110_000
    # no single airport to measure from ("欧洲"): the whole band table comes back
    bands = partner(fares, "JL", "東京", "欧洲", "PY", partner_program="jmb_partner")
    assert len(bands.results) == 13 and bands.total_miles is None and "総旅程距離" in bands.message
    assert (
        partner(fares, "JL", "東京", "ロンドン", "PY", partner_program="oneworld").status
        == "not_found"
    )


def test_areas() -> None:
    assert resolve_area("欧洲") is not None and resolve_area("欧洲").code == "EUROPE"  # type: ignore[union-attr]
    assert resolve_area("中國").code == "CN"  # type: ignore[union-attr]
    assert resolve_area("東京") is None


def test_jal_partner_distance_is_estimated_from_airports(fares: FareStore) -> None:
    sin = partner(
        fares, "JL", "東京", "シンガポール", "C", trip_type="RT", partner_program="oneworld"
    )
    assert sin.total_miles == 80_000 and "HND–SIN" in (sin.calculation or "")
    lon = partner(fares, "JL", "東京", "ロンドン", "C", trip_type="RT", partner_program="oneworld")
    # ~11,900 miles is within 3% of the 12,000 edge: both bands, no single total
    assert [r["miles"] for r in lon.results] == [110_000, 115_000] and lon.total_miles is None
