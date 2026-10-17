"""Required-miles calculation from structured award charts (design.md §3.1).

Everything here is deterministic: the LLM only chooses arguments and phrases the
answer. Results always carry the chart version and source so answers can cite them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from milewise.db import Db
from milewise.locations import City, airport_hint, resolve_city

JST = timezone(timedelta(hours=9))
JAPAN_ZONE = "1"
SUPPORTED_FAMILIES = {("NH", "own"): "NH-INTL-OWN", ("JL", "own"): "JL-INTL-OWN"}
ROUTE_FAMILIES = {"JL-INTL-OWN"}  # priced by route label, not by zone pair

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
    totals: dict[str, int] = field(default_factory=dict)  # route charts: {"min": .., "max": ..}
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
    flight_number: str | None = None,
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
    if family in ROUTE_FAMILIES:
        return _route_required_miles(
            db,
            family,
            base,
            origin,
            destination,
            cabin,
            trip_type,
            outbound_date,
            return_date,
            booking_date or today_jst(),
            flight_number,
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


# ---------------------------------------------------------------- route-based (JAL)

ROUTE_CAVEATS = [
    "基本マイル数は最少のマイル数。空席状況により特典航空券PLUS（追加マイル）となり、最大で PLUS 欄のマイル数まで増える",
    "必要マイル数は日程ごとに JAL Web サイトの空席照会で確定する",
]


def _route_tokens(query: str, city: City) -> list[str]:
    """Match tokens for a route row: a named airport narrows the match to that airport."""
    hint = airport_hint(query)
    if hint and hint in city.airports:
        return [f"airport:{hint}", f"country:{city.country}"]
    return [
        f"city:{city.city_code}",
        f"country:{city.country}",
        *(f"airport:{a}" for a in city.airports),
    ]


def season_for_group(db: Db, family: str, group: str, day: date) -> str | None:
    rows = db.query(
        "SELECT season FROM fare.season_calendar WHERE chart_family = :f AND season_group = :g"
        " AND CAST(:d AS date) BETWEEN date_from AND date_to",
        {"f": family, "g": group, "d": day.isoformat()},
    )
    return rows[0]["season"] if rows else None


def _route_required_miles(
    db: Db,
    family: str,
    base: dict[str, Any],
    origin: str,
    destination: str,
    cabin: str,
    trip_type: str,
    outbound_date: date | None,
    return_date: date | None,
    booking: date,
    flight_number: str | None,
) -> MilesResult:
    res = MilesResult("ok", origin=Endpoint(origin), destination=Endpoint(destination), **base)
    cities = {}
    for label, query, ep in (
        ("origin", origin, res.origin),
        ("destination", destination, res.destination),
    ):
        cands = resolve_city(query)
        if len(cands) > 1:
            res.status = "needs_clarification"
            res.candidates[label] = [f"{c.name_ja} ({c.city_code})" for c in cands]
        elif not cands:
            res.status = "not_found"
            res.message += f"{label}「{query}」を都市として認識できません。"
        else:
            c = cands[0]
            assert ep is not None
            ep.city_code, ep.name_ja, ep.airports = c.city_code, c.name_ja, list(c.airports)
            cities[label] = (query, c)
    if res.status != "ok":
        return res

    (o_q, o_city), (d_q, d_city) = cities["origin"], cities["destination"]
    if (o_city.country == "JP") == (d_city.country == "JP"):
        res.status = "not_found"
        res.message = (
            "JAL国際線特典航空券のチャートは日本発着の国際線のみです（国内線・海外発着は対象外）。"
        )
        return res
    (jp_q, jp_city), (fx_q, fx_city) = (
        ((o_q, o_city), (d_q, d_city)) if o_city.country == "JP" else ((d_q, d_city), (o_q, o_city))
    )
    rows = db.query(
        "SELECT c.chart_id, c.price_kind, c.booking_from, c.source_url, c.fetched_at, r.route_label,"
        " r.flight_numbers, r.season, r.miles, r.source_ref"
        " FROM fare.award_rates r JOIN fare.award_charts c USING (chart_id)"
        " WHERE c.chart_family = :f AND c.booking_from <= CAST(:b AS date)"
        " AND (c.booking_to IS NULL OR c.booking_to >= CAST(:b AS date)) AND r.cabin = :cabin"
        " AND r.origin_match && string_to_array(:jp, ',') AND r.dest_match && string_to_array(:fx, ',')"
        " ORDER BY c.price_kind DESC, r.route_label, array_position(ARRAY['L','R','H','ANY'], r.season)",
        {
            "f": family,
            "b": booking.isoformat(),
            "cabin": cabin,
            "jp": ",".join(_route_tokens(jp_q, jp_city)),
            "fx": ",".join(_route_tokens(fx_q, fx_city)),
        },
    )
    # Some routes are priced per flight (バンコク JL031/JL034便). With a flight number the
    # matching flight-specific rows replace the general ones of the same chart; without one,
    # all rows are kept and returned as variants.
    if flight_number:
        flight = flight_number.upper().replace(" ", "")
        specific = [r for r in rows if r["flight_numbers"] and flight in r["flight_numbers"]]
        covered = {r["chart_id"] for r in specific}
        rows = specific + [
            r for r in rows if not r["flight_numbers"] and r["chart_id"] not in covered
        ]
    priced = [r for r in rows if r["price_kind"] in ("floor", "fixed")]
    if not priced:
        res.status = "not_found"
        res.message = f"{fx_city.name_ja} 路線の {cabin} クラスは JAL 国際線特典航空券の設定がありません（－）。"
        return res

    res.price_kind = "fixed" if cabin == "F" else "floor"
    res.results = [
        {
            "variant": r["route_label"],
            "season": r["season"],
            "miles": r["miles"],
            "price_kind": r["price_kind"],
            **({"flights": r["flight_numbers"]} if r["flight_numbers"] else {}),
        }
        for r in rows
    ]
    first = rows[0]
    res.chart = {
        "chart_ids": sorted({r["chart_id"] for r in rows}),
        "booking_from": first["booking_from"].isoformat(),
        "source_url": first["source_url"],
        "fetched_at": first["fetched_at"].astimezone(JST).isoformat(),
        "source_refs": [r["source_ref"] for r in rows],
    }
    res.caveats = list(STANDARD_CAVEATS) + (ROUTE_CAVEATS if cabin != "F" else [])
    variants = sorted({r["route_label"] for r in priced})
    if len(variants) > 1:
        res.caveats.append(
            "出発空港・航空会社便名によってマイル数が異なる路線: "
            + " / ".join(variants)
            + "。空港または便名が分かれば1つに絞れる"
        )

    # One-way charts: round trip = outbound leg + return leg (JAL 必要なマイル数 page).
    legs = 2 if trip_type == "RT" else 1
    if len(variants) == 1:
        if cabin != "F":
            low = next(r["miles"] for r in priced)
            highs = [r["miles"] for r in rows if r["price_kind"] == "ceiling"]
            res.totals = {"min": low * legs, **({"max": max(highs) * legs} if highs else {})}
            res.calculation = f"片道 基本 {low:,}" + (
                f" × 2（往復は片道の合算）= {low * 2:,}" if legs == 2 else ""
            )
            if highs:
                res.calculation += f"; PLUS 最大 {max(highs) * legs:,}"
        else:
            by_season = {r["season"]: r["miles"] for r in priced}
            dates = [outbound_date, return_date][:legs]
            seasons = [season_for_group(db, family, "F", d) if d else None for d in dates]
            for key, d, s in zip(("outbound", "return"), dates, seasons, strict=False):
                if d:
                    res.seasons_for_dates[key] = s
                    if s is None:
                        res.caveats.append(
                            f"{d} のファーストクラスのシーズンは収録カレンダー外のため判定できません"
                        )
            if all(seasons) and all(s in by_season for s in seasons):
                parts = [by_season[str(s)] for s in seasons]
                res.total_miles = sum(parts)
                res.calculation = " + ".join(
                    f"{s}: {p:,}" for s, p in zip(seasons, parts, strict=True)
                )
                res.calculation += f" = {res.total_miles:,}"
    return res
