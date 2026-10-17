"""Golden tests for JAL (route-based) required miles.

Expected values were read from the saved official page
(https://www.jal.co.jp/jp/ja/jalmile/use/jal/inter/routemiles.html, 更新日 2026-04-16) with a
plain regex over the raw HTML, independent of milewise.charts.jal.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from milewise.db import Db
from milewise.tools.miles import get_required_miles

BOOKING = date(2026, 10, 17)


def by_kind(res: Any, kind: str) -> dict[str, int]:
    return {
        f"{r['variant']}|{r['season']}": r["miles"] for r in res.results if r["price_kind"] == kind
    }


def test_tokyo_london_business_one_way_is_a_floor_with_plus_ceiling(db: Db) -> None:
    res = get_required_miles(
        db, "JL", "東京", "ロンドン", "C", trip_type="OW", booking_date=BOOKING
    )
    assert res.status == "ok" and res.price_kind == "floor"
    assert by_kind(res, "floor") == {"ロンドン|ANY": 57_000}
    assert by_kind(res, "ceiling") == {"ロンドン|ANY": 441_000}
    assert res.totals == {"min": 57_000, "max": 441_000}
    assert any("PLUS" in c for c in res.caveats)
    assert res.chart["source_url"].startswith("https://www.jal.co.jp/")  # type: ignore[index]


def test_round_trip_is_the_sum_of_two_one_way_legs(db: Db) -> None:
    res = get_required_miles(
        db, "JL", "東京", "ロンドン", "C", trip_type="RT", booking_date=BOOKING
    )
    assert res.totals == {"min": 114_000, "max": 882_000}


def test_first_class_by_season_and_round_trip_with_dates(db: Db) -> None:
    res = get_required_miles(db, "JL", "东京", "伦敦", "F", trip_type="OW", booking_date=BOOKING)
    assert by_kind(res, "fixed") == {
        "ロンドン|L": 110_000,
        "ロンドン|R": 125_000,
        "ロンドン|H": 140_000,
    }
    rt = get_required_miles(
        db,
        "JL",
        "東京",
        "ロンドン",
        "F",
        trip_type="RT",
        outbound_date=date(2026, 8, 1),  # H: 7/9–8/25
        return_date=date(2026, 10, 1),  # R: 8/26–12/12
        booking_date=BOOKING,
    )
    assert rt.seasons_for_dates == {"outbound": "H", "return": "R"}
    assert rt.total_miles == 265_000


def test_first_class_not_offered(db: Db) -> None:
    res = get_required_miles(db, "JL", "東京", "フランクフルト", "F", booking_date=BOOKING)
    assert res.status == "not_found"


def test_city_input_lists_airport_variants_instead_of_picking_one(db: Db) -> None:
    res = get_required_miles(db, "JL", "東京", "台北", "Y", trip_type="OW", booking_date=BOOKING)
    assert by_kind(res, "floor") == {
        "台北－東京（羽田）便|ANY": 11_000,
        "台北－東京（成田）、大阪（関西）、名古屋（中部）便|ANY": 9_000,
    }
    assert res.totals == {}  # ambiguous: no single total
    assert any("出発空港" in c for c in res.caveats)


def test_named_airport_narrows_the_route(db: Db) -> None:
    res = get_required_miles(db, "JL", "羽田", "台北", "Y", trip_type="OW", booking_date=BOOKING)
    assert by_kind(res, "floor") == {"台北－東京（羽田）便|ANY": 11_000}
    pvg = get_required_miles(db, "JL", "東京", "浦東", "Y", trip_type="OW", booking_date=BOOKING)
    assert by_kind(pvg, "floor") == {"上海（浦東）便|ANY": 10_000}


def test_flight_number_selects_the_flight_specific_row(db: Db) -> None:
    both = get_required_miles(
        db, "JL", "東京", "バンコク", "Y", trip_type="OW", booking_date=BOOKING
    )
    assert set(by_kind(both, "floor").values()) == {13_500, 17_500}
    jl031 = get_required_miles(
        db,
        "JL",
        "東京",
        "バンコク",
        "Y",
        trip_type="OW",
        flight_number="JL031",
        booking_date=BOOKING,
    )
    assert by_kind(jl031, "floor") == {"バンコク JL031/JL034便|ANY": 17_500}
    other = get_required_miles(
        db,
        "JL",
        "東京",
        "バンコク",
        "Y",
        trip_type="OW",
        flight_number="JL033",
        booking_date=BOOKING,
    )
    assert by_kind(other, "floor") == {
        "バンコク－東京（羽田・成田）便 ※JL031/JL034便以外|ANY": 13_500
    }
    assert by_kind(other, "ceiling") == {"バンコク－東京（羽田・成田）便|ANY": 101_000}


def test_inbound_direction_and_osaka_origin(db: Db) -> None:
    res = get_required_miles(
        db, "JL", "バンコク", "大阪", "C", trip_type="OW", booking_date=BOOKING
    )
    assert by_kind(res, "floor") == {"バンコク－大阪（関西）、名古屋（中部）便|ANY": 37_500}


def test_japan_origin_or_destination_only(db: Db) -> None:
    assert (
        get_required_miles(db, "JL", "ソウル", "バンコク", "Y", booking_date=BOOKING).status
        == "not_found"
    )
    assert (
        get_required_miles(db, "JL", "東京", "大阪", "Y", booking_date=BOOKING).status
        == "not_found"
    )
