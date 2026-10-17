"""get_season for a period: the date ranges an answer is grouped by (eval miles-12)."""

from __future__ import annotations

from datetime import date

from milewise.agent import season_periods
from milewise.charts.store import FareStore


def test_tokyo_taipei_july_august_2027(fares: FareStore) -> None:
    res = season_periods(fares, "NH", "台北", date(2027, 7, 1), date(2027, 8, 31))
    assert res["status"] == "ok" and res["season_group"] == "Z2-3-4"
    assert res["segments"] == [
        {"date_from": "2027-07-01", "date_to": "2027-07-15", "season": "R"},
        {"date_from": "2027-07-16", "date_to": "2027-08-23", "season": "H"},
        {"date_from": "2027-08-24", "date_to": "2027-08-31", "season": "R"},
    ]
    assert res["chart"]["source_url"].startswith("https://www.ana.co.jp/")
    assert "caveat" not in res


def test_taiwan_resolves_to_taipei(fares: FareStore) -> None:
    res = season_periods(fares, "NH", "台湾", date(2027, 7, 1), None)
    assert res["segments"] == [{"date_from": "2027-07-01", "date_to": "2027-07-01", "season": "R"}]


def test_year_end_split_is_merged_and_uncovered_days_flagged(fares: FareStore) -> None:
    res = season_periods(fares, "NH", "台北", date(2026, 12, 25), date(2027, 1, 2))
    assert res["segments"] == [{"date_from": "2026-12-25", "date_to": "2027-01-02", "season": "H"}]
    late = season_periods(fares, "NH", "台北", date(2028, 3, 1), date(2028, 6, 30))
    assert "caveat" in late


def test_jal_seasons_apply_to_first_class_only(fares: FareStore) -> None:
    res = season_periods(fares, "JL", "ロンドン", date(2026, 8, 1), date(2026, 8, 10))
    assert res["season_group"] == "F" and "ファーストクラスのみ" in res["note"]
