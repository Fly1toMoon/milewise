"""Required-miles calculation from structured award charts (design.md §3.1).

Everything here is deterministic: the LLM only chooses arguments and phrases the
answer. Results always carry the chart version and source so answers can cite them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from milewise.db import Db
from milewise.locations import City, resolve_city

JST = timezone(timedelta(hours=9))
JAPAN_ZONE = "1"
SUPPORTED_FAMILIES = {("NH", "own"): "NH-INTL-OWN"}

Status = Literal["ok", "needs_clarification", "not_found", "unsupported"]

STANDARD_CAVEATS = [
    "燃油特別付加運賃・税金・空港使用料は別途必要（マイル数に含まれない）",
    "特典航空券の空席は別途確認が必要（必要マイル数は空席を保証しない）",
]


@dataclass
class Endpoint:
    input: str
    city_code: str | None = None
    name_ja: str | None = None
    airports: list[str] = field(default_factory=list)
    zone: str | None = None


@dataclass
class MilesResult:
    status: Status
    message: str = ""
    airline: str | None = None
    award_type: str | None = None
    trip_type: str | None = None
    cabin: str | None = None
    origin: Endpoint | None = None
    destination: Endpoint | None = None
    results: list[dict[str, Any]] = field(
        default_factory=list
    )  # [{"season": "L", "miles": 110000}]
    seasons_for_dates: dict[str, str | None] = field(default_factory=dict)
    total_miles: int | None = None
    calculation: str | None = None
    price_kind: str | None = None
    chart: dict[str, Any] | None = None
    candidates: dict[str, list[str]] = field(default_factory=dict)
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in (None, [], {}, "")}


def today_jst() -> date:
    return datetime.now(JST).date()


def _endpoint(db: Db, family: str, query: str) -> tuple[Endpoint, list[City]]:
    cities = resolve_city(query)
    ep = Endpoint(input=query)
    if len(cities) != 1:
        return ep, cities
    c = cities[0]
    ep.city_code, ep.name_ja, ep.airports = c.city_code, c.name_ja, list(c.airports)
    rows = db.query(
        "SELECT zone_code, match_kind FROM fare.zone_members WHERE chart_family = :f AND ("
        " (match_kind = 'airport' AND match_value = ANY(string_to_array(:airports, ',')))"
        " OR (match_kind = 'city' AND match_value = :city)"
        " OR (match_kind = 'country' AND match_value = :country))",
        {"f": family, "airports": ",".join(c.airports), "city": c.city_code, "country": c.country},
    )
    rank = {"airport": 0, "city": 1, "country": 2}
    if rows:
        ep.zone = min(rows, key=lambda r: rank[r["match_kind"]])["zone_code"]
    return ep, cities


def season_for(db: Db, family: str, zone_a: str, zone_b: str, day: date) -> str | None:
    """Season of a travel date. Calendars exist only for itineraries touching Japan."""
    if JAPAN_ZONE not in (zone_a, zone_b) or zone_a == zone_b:
        return None
    other = zone_b if zone_a == JAPAN_ZONE else zone_a
    rows = db.query(
        "SELECT s.season FROM fare.season_calendar s JOIN fare.zones z"
        " ON z.chart_family = s.chart_family AND z.season_group = s.season_group"
        " WHERE s.chart_family = :f AND z.zone_code = :zone"
        " AND CAST(:d AS date) BETWEEN s.date_from AND s.date_to",
        {"f": family, "zone": other, "d": day.isoformat()},
    )
    return rows[0]["season"] if rows else None


def get_required_miles(
    db: Db,
    airline: str,
    origin: str,
    destination: str,
    cabin: str,
    trip_type: str = "RT",
    outbound_date: date | None = None,
    return_date: date | None = None,
    booking_date: date | None = None,
    award_type: str = "own",
) -> MilesResult:
    base: dict[str, Any] = {
        "airline": airline,
        "award_type": award_type,
        "trip_type": trip_type,
        "cabin": cabin,
    }
    family = SUPPORTED_FAMILIES.get((airline, award_type))
    if family is None:
        return MilesResult(
            "unsupported",
            f"{airline} の {award_type} 特典の必要マイルチャートは未収録です",
            **base,
        )

    o, o_cands = _endpoint(db, family, origin)
    d, d_cands = _endpoint(db, family, destination)
    res = MilesResult("ok", origin=o, destination=d, **base)
    for label, ep, cands in (("origin", o, o_cands), ("destination", d, d_cands)):
        if len(cands) > 1:
            res.status = "needs_clarification"
            res.candidates[label] = [f"{c.name_ja} ({c.city_code})" for c in cands]
        elif not cands:
            res.status = "not_found"
            res.message += f"{label}「{ep.input}」を都市として認識できません。"
        elif ep.zone is None:
            res.status = "not_found"
            res.message += f"{ep.name_ja} は {airline} の国際線特典ゾーン表にありません。"
    if res.status != "ok":
        return res
    assert o.zone is not None and d.zone is not None
    if o.zone == d.zone:
        res.status = "not_found"
        res.message = "同一ゾーン内（国内線など）の旅程はこのチャートの対象外です。"
        return res

    booking = booking_date or today_jst()
    charts = db.query(
        "SELECT chart_id, booking_from, source_url, fetched_at, price_kind FROM fare.award_charts"
        " WHERE chart_family = :f AND trip_type = :t AND booking_from <= CAST(:b AS date)"
        " AND (booking_to IS NULL OR booking_to >= CAST(:b AS date))",
        {"f": family, "t": trip_type, "b": booking.isoformat()},
    )
    if not charts:
        res.status = "not_found"
        res.message = f"予約・発券日 {booking} に適用されるチャートが収録されていません。"
        return res
    chart = charts[0]
    res.price_kind = chart["price_kind"]
    res.chart = {
        "chart_id": chart["chart_id"],
        "booking_from": chart["booking_from"].isoformat(),
        "source_url": chart["source_url"],
        "fetched_at": chart["fetched_at"].astimezone(JST).isoformat(),
    }

    rows = db.query(
        "SELECT season, miles, source_ref FROM fare.award_rates WHERE chart_id = :c AND cabin = :cabin"
        " AND ((origin_zone = :a AND dest_zone = :b) OR (origin_zone = :b AND dest_zone = :a))"
        " ORDER BY array_position(ARRAY['L','R','H','ANY'], season)",
        {"c": chart["chart_id"], "cabin": cabin, "a": o.zone, "b": d.zone},
    )
    if not rows:
        res.status = "not_found"
        res.message = f"Zone{o.zone}–Zone{d.zone} の {cabin} クラスは特典航空券の設定がありません（設定なし）。"
        return res
    by_season = {r["season"]: r["miles"] for r in rows}
    res.results = [{"season": r["season"], "miles": r["miles"]} for r in rows]
    res.chart["source_refs"] = [r["source_ref"] for r in rows]
    res.caveats = list(STANDARD_CAVEATS)

    if outbound_date:
        s_out = season_for(db, family, o.zone, d.zone, outbound_date)
        res.seasons_for_dates["outbound"] = s_out
        if s_out is None:
            res.caveats.append(f"{outbound_date} のシーズンは収録カレンダー外のため判定できません")
        if trip_type == "OW" and s_out:
            res.total_miles = by_season[s_out]
            res.calculation = f"片道 {s_out} シーズン = {res.total_miles:,}"
        elif trip_type == "RT" and return_date:
            s_ret = season_for(db, family, o.zone, d.zone, return_date)
            res.seasons_for_dates["return"] = s_ret
            if s_ret is None:
                res.caveats.append(
                    f"{return_date} のシーズンは収録カレンダー外のため判定できません"
                )
            if s_out and s_ret:
                if s_out == s_ret:
                    res.total_miles = by_season[s_out]
                    res.calculation = f"往路・復路とも {s_out} シーズン = {res.total_miles:,}"
                else:
                    # ANA rule: halve each leg's round-trip value and add them up.
                    half_out, half_ret = by_season[s_out] // 2, by_season[s_ret] // 2
                    res.total_miles = half_out + half_ret
                    res.calculation = (
                        f"往路 {s_out}: {by_season[s_out]:,}÷2={half_out:,} + "
                        f"復路 {s_ret}: {by_season[s_ret]:,}÷2={half_ret:,} = {res.total_miles:,}"
                    )
        elif trip_type == "RT" and s_out:
            res.caveats.append(
                "復路の日付が未指定: 往路と復路でシーズンが異なる場合は各シーズンの往復マイルの1/2を合算"
            )
    return res
