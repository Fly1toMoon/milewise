"""Golden tests for get_required_miles against the committed ANA chart.

Expected values were read by hand from the official page
(https://www.ana.co.jp/ja/jp/guide/amc/award/international/terms/, fetched 2026-10-17),
including ANA's own worked example for mixed seasons. They are deliberately not
derived from the parser output, so a parser regression shows up here.
"""

from __future__ import annotations

from datetime import date

import pytest

from milewise.db import Db
from milewise.tools.miles import get_required_miles, season_for

BOOKING = date(2026, 10, 17)


def miles(res: object) -> dict[str, int]:
    return {r["season"]: r["miles"] for r in res.results}  # type: ignore[attr-defined]


def test_tokyo_london_business_round_trip(db: Db) -> None:
    res = get_required_miles(db, "NH", "東京", "ロンドン", "C", booking_date=BOOKING)
    assert res.status == "ok"
    assert miles(res) == {"L": 110_000, "R": 115_000, "H": 180_000}
    assert res.origin.zone == "1" and res.destination.zone == "7"  # type: ignore[union-attr]
    assert res.chart["chart_id"] == "NH-INTL-OWN-RT@2025-06-24"  # type: ignore[index]
    assert res.chart["source_url"].startswith("https://www.ana.co.jp/")  # type: ignore[index]


def test_one_way_is_its_own_chart(db: Db) -> None:
    res = get_required_miles(db, "NH", "Tokyo", "London", "C", trip_type="OW", booking_date=BOOKING)
    assert miles(res) == {"L": 55_000, "R": 57_500, "H": 90_000}
    assert res.chart["chart_id"] == "NH-INTL-OWN-OW@2025-06-24"  # type: ignore[index]


@pytest.mark.parametrize(
    ("origin", "destination"),
    [("东京", "伦敦"), ("倫敦", "東京"), ("HND", "LHR"), ("羽田空港", "Heathrow")],
)
def test_multilingual_and_reverse_direction(db: Db, origin: str, destination: str) -> None:
    res = get_required_miles(db, "NH", origin, destination, "C", booking_date=BOOKING)
    assert res.status == "ok"
    assert miles(res)["L"] == 110_000


def test_ana_worked_example_mixed_seasons(db: Db) -> None:
    # ANA's example: 東京⇒ロサンゼルス⇒東京, outbound L, return R, economy
    # → 40,000÷2 + 50,000÷2 = 45,000.
    res = get_required_miles(
        db,
        "NH",
        "東京",
        "ロサンゼルス",
        "Y",
        outbound_date=date(2026, 2, 20),  # L for 日本–北米/欧州 calendar
        return_date=date(2026, 3, 5),  # R
        booking_date=BOOKING,
    )
    assert res.seasons_for_dates == {"outbound": "L", "return": "R"}
    assert miles(res)["L"] == 40_000 and miles(res)["R"] == 50_000
    assert res.total_miles == 45_000


def test_same_season_round_trip_uses_full_value(db: Db) -> None:
    res = get_required_miles(
        db,
        "NH",
        "東京",
        "ロサンゼルス",
        "Y",
        outbound_date=date(2026, 2, 20),
        return_date=date(2026, 2, 27),
        booking_date=BOOKING,
    )
    assert res.total_miles == 40_000


def test_season_calendars_differ_by_region(db: Db) -> None:
    # 2026-08-01: H for the Asia calendar (7/18–8/23) and for 北米/欧州 (7/16–8/23).
    assert season_for(db, "NH-INTL-OWN", "1", "2", date(2026, 8, 1)) == "H"
    # 2026-02-20: R for the Asia calendar (2/14–3/31) but L for 北米/欧州 (1/6–2/28).
    assert season_for(db, "NH-INTL-OWN", "1", "3", date(2026, 2, 20)) == "R"
    assert season_for(db, "NH-INTL-OWN", "1", "6", date(2026, 2, 20)) == "L"


def test_every_published_day_has_exactly_one_season(db: Db) -> None:
    rows = db.query(
        "SELECT z.zone_code, d::date AS day, count(s.*) AS n"
        " FROM fare.zones z CROSS JOIN generate_series('2026-01-01'::date, '2027-12-31', '1 day') d"
        " LEFT JOIN fare.season_calendar s ON s.chart_family = z.chart_family"
        " AND s.season_group = z.season_group AND d BETWEEN s.date_from AND s.date_to"
        " WHERE z.season_group IS NOT NULL GROUP BY 1, 2 HAVING count(s.*) <> 1"
    )
    assert rows == []


def test_date_outside_calendar_is_flagged_not_guessed(db: Db) -> None:
    res = get_required_miles(
        db,
        "NH",
        "東京",
        "ロンドン",
        "C",
        trip_type="OW",
        outbound_date=date(2029, 1, 10),
        booking_date=BOOKING,
    )
    assert res.status == "ok" and res.total_miles is None
    assert any("カレンダー外" in c for c in res.caveats)


def test_cabin_not_offered(db: Db) -> None:
    res = get_required_miles(db, "NH", "東京", "ソウル", "F", booking_date=BOOKING)
    assert res.status == "not_found" and "設定なし" in res.message


def test_unknown_city(db: Db) -> None:
    res = get_required_miles(db, "NH", "東京", "火星", "C", booking_date=BOOKING)
    assert res.status == "not_found"


def test_domestic_is_out_of_scope(db: Db) -> None:
    res = get_required_miles(db, "NH", "東京", "大阪", "Y", booking_date=BOOKING)
    assert res.status == "not_found"


def test_booking_before_chart_version(db: Db) -> None:
    res = get_required_miles(db, "NH", "東京", "ロンドン", "C", booking_date=date(2025, 1, 1))
    assert res.status == "not_found"


def test_partner_awards_not_loaded_yet(db: Db) -> None:
    for airline in ("NH", "JL"):
        res = get_required_miles(
            db, airline, "東京", "ロンドン", "C", award_type="partner", booking_date=BOOKING
        )
        assert res.status == "unsupported"
