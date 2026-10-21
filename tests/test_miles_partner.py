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


def test_jal_partner_itinerary_adds_up_every_leg(fares: FareStore) -> None:
    # The session that found the gap: Tokyo-Hong Kong-London round trip is 15,576, not 11,920.
    res = partner(
        fares,
        "JL",
        "東京",
        "ロンドン",
        "C",
        trip_type="RT",
        itinerary=[["东京", "香港", "伦敦", "香港", "东京"]],
    )
    assert res.route == "HND→HKG→LHR→HKG→HND"
    assert "15,576" in (res.calculation or "")
    assert "HND–HKG 1,804 + HKG–LHR 5,984" in (res.calculation or "")
    by_prog = {r["variant"].split(":")[0]: r["miles"] for r in res.results}
    assert by_prog == {"oneworld": 120_000, "jmb_partner": 130_000}


def test_jal_partner_open_jaw_does_not_count_the_land_part(fares: FareStore) -> None:
    res = partner(
        fares,
        "JL",
        "東京",
        "ロンドン",
        "Y",
        itinerary=[["東京", "ロンドン"], ["パリ", "東京"]],
        partner_program="oneworld",
    )
    assert res.route == "HND→LHR ／ 地上移動 LHR–CDG ／ CDG→HND"
    assert "地上移動 LHR–CDG は距離に含めない" in (res.calculation or "")
    assert "LHR–CDG" not in (res.calculation or "").split("。")[0]  # not in the sum


def test_jal_partner_many_connections_and_segment_limit(fares: FareStore) -> None:
    trip = ["東京", "香港", "ドーハ", "マドリード", "ロンドン", "ヘルシンキ", "東京"]
    res = partner(fares, "JL", "東京", "ロンドン", "C", itinerary=[trip])
    assert res.route == "HND→HKG→DOH→MAD→LHR→HEL→HND"
    assert not any("上限" in c for c in res.caveats)  # 6 segments: within both limits
    longer = partner(
        fares, "JL", "東京", "ロンドン", "C", itinerary=[[*trip[:-1], "ドバイ", "東京"]]
    )
    assert any("jmb_partner: 飛行区間 7 は1特典の上限 6 区間を超える" in c for c in longer.caveats)
    assert not any(c.startswith("oneworld:") for c in longer.caveats)


def test_jal_partner_unknown_place_asks(fares: FareStore) -> None:
    res = partner(
        fares, "JL", "東京", "ロンドン", "C", itinerary=[["東京", "アトランティス", "ロンドン"]]
    )
    assert res.status == "needs_clarification" and "アトランティス" in res.message


def test_jal_partner_round_trip_given_as_outbound_goes_back_the_same_way(fares: FareStore) -> None:
    res = partner(
        fares, "JL", "東京", "ロンドン", "C", trip_type="RT", itinerary=[["东京", "香港", "伦敦"]]
    )
    assert res.route == "HND→HKG→LHR→HKG→HND" and "15,576" in (res.calculation or "")


def test_jal_partner_stretches_that_meet_are_one_journey(fares: FareStore) -> None:
    res = partner(
        fares,
        "JL",
        "東京",
        "ロンドン",
        "C",
        itinerary=[["東京", "香港", "倫敦"], ["倫敦", "香港", "東京"]],
    )
    assert res.route == "HND→HKG→LHR→HKG→HND" and "地上移動" not in (res.calculation or "")


def test_jal_partner_any_airport_by_iata_code(fares: FareStore) -> None:
    # Santiago is not in the city list: its IATA code still works, with the name shown
    res = partner(
        fares, "JL", "東京", "SCL", "Y", trip_type="OW", itinerary=[["東京", "DFW", "リマ", "SCL"]]
    )
    assert res.route == (
        "HND→DFW→LIM→SCL（Comodoro Arturo Merino Benítez International Airport, Santiago）"
    )
    assert res.status == "ok" and "LIM–SCL" in (res.calculation or "")


def test_city_names_do_not_resolve_through_the_airport_table() -> None:
    from milewise.locations import resolve_city
    from milewise.tools.miles import _airport

    # OurAirports also has London, Ontario (YXU) and Sydney, Nova Scotia (YQY)
    assert [c.city_code for c in resolve_city("London")] == ["LON"]
    assert _airport("ロンドン") == "LHR" and _airport("悉尼") == "SYD"
    assert _airport("YXU") == "YXU"  # only an explicit code reaches the full table
    assert _airport("Lima") == "LIM"  # Lima is in the city list
    assert _airport("Santiago") is None  # by name: unknown, so the tool asks
