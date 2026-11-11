"""itinerary_facts: counts only, never a verdict (design.md §4.13)."""

from __future__ import annotations

from milewise.charts.store import FareStore
from milewise.tools.miles import get_required_miles, itinerary_facts

# The shared post (findings 2026-10-22): MNL-KUL, by land KUL-SIN, then SIN-KUL-SHA-OSA-HEL-SEL-TYO
POST = [["马尼拉", "吉隆坡"], ["新加坡", "吉隆坡", "上海", "大阪", "赫尔辛基", "首尔", "东京"]]
# multi-07: HKG-TYO-LON, by land LON-PAR, then PAR-SHA-TYO
MULTI_07 = [["香港", "东京", "伦敦"], ["巴黎", "上海", "东京"]]


def test_post_itinerary() -> None:
    f = itinerary_facts(POST)
    assert f["flown_segments"] == 7
    assert f["ground_transfers"] == 1
    assert f["ground_transfer_list"][0] == {
        "after_segment": 1,
        "from": "KUL",
        "to": "SIN",
        "from_country": "MY",
        "to_country": "SG",
    }
    assert f["japan_domestic_segments"] == 0  # SHA-OSA and OSA-HEL are international
    assert f["origin_country"] == "PH" and f["final_country"] == "JP"
    assert f["countries_in_order"] == ["PH", "MY", "SG", "MY", "CN", "JP", "FI", "KR", "JP"]
    assert f["returns_to_origin_country"] is None
    assert f["city_visits"]["KUL"] == 2
    assert len(f["notes"]) == 1  # no CN / HK question here
    assert "allowed" not in f and "ok" not in f  # no verdict


def test_multi_07_itinerary() -> None:
    f = itinerary_facts(MULTI_07)
    assert f["flown_segments"] == 4 and f["ground_transfers"] == 1
    assert f["japan_domestic_segments"] == 0
    assert f["origin_country"] == "HK" and f["final_country"] == "JP"
    assert f["segments"][2]["to_country"] == "CN"  # the 3rd segment reaches mainland China
    assert f["returns_to_origin_country"] is None  # HK and CN are different codes ...
    assert any("CN" in n and "HK" in n and "定義していない" in n for n in f["notes"])  # ... flagged


def test_return_to_origin_country_and_domestic_segments() -> None:
    f = itinerary_facts([["东京", "香港", "大阪", "札幌", "伦敦"]])
    assert f["returns_to_origin_country"] == {
        "segment": 2,
        "airport": "KIX",
        "countries_after": ["JP", "GB"],
    }
    assert f["japan_domestic_segments"] == 1


def test_unknown_place_is_reported() -> None:
    assert itinerary_facts([["东京", "どこか"]]) == {"unresolved": "どこか"}


def test_facts_come_with_the_jal_partner_result(fares: FareStore) -> None:
    res = get_required_miles(
        fares, "JL", "马尼拉", "东京", "C", trip_type="OW", award_type="partner", itinerary=POST
    )
    assert res.status == "ok" and res.itinerary_facts["flown_segments"] == 7
    plain = get_required_miles(
        fares, "JL", "香港", "伦敦", "C", trip_type="OW", award_type="partner"
    )
    assert "itinerary_facts" not in plain.to_dict()
