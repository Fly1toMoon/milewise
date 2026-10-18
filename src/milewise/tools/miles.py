"""Required-miles calculation from structured award charts (design.md §3.1).

Everything here is deterministic: the LLM only chooses arguments and phrases the
answer. Results always carry the chart version and source so answers can cite them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from milewise.charts.store import SEASON_ORDER, FareStore
from milewise.locations import (
    City,
    airport_hint,
    great_circle_miles,
    resolve_area,
    resolve_city,
)

JST = timezone(timedelta(hours=9))
JAPAN_ZONE = "1"
SUPPORTED_FAMILIES = {("NH", "own"): "NH-INTL-OWN", ("JL", "own"): "JL-INTL-OWN"}
ROUTE_FAMILIES = {"JL-INTL-OWN"}  # priced by route label, not by zone pair
NH_PARTNER = "NH-INTL-PARTNER"  # zone pairs, Japan split into Zone 1-A / 1-B, no seasons
# JAL partner awards are distance-based (総旅程距離); which chart applies depends on the
# operating airline: oneworld members or other JMB partners.
JL_PARTNER = {"oneworld": "JL-INTL-OW", "jmb_partner": "JL-INTL-JMBP"}

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


def _endpoint(fares: FareStore, family: str, query: str) -> tuple[Endpoint, list[City]]:
    cities = resolve_city(query)
    ep = Endpoint(input=query)
    if len(cities) != 1:
        return ep, cities
    c = cities[0]
    ep.city_code, ep.name_ja, ep.airports = c.city_code, c.name_ja, list(c.airports)
    ep.zone = fares.zone_for(family, list(c.airports), c.city_code, c.country)
    return ep, cities


def season_group_for(fares: FareStore, family: str, zone_a: str, zone_b: str) -> str | None:
    """Calendars exist only for itineraries with Japan at one end."""
    if JAPAN_ZONE not in (zone_a, zone_b) or zone_a == zone_b:
        return None
    return fares.season_group(family, zone_b if zone_a == JAPAN_ZONE else zone_a)


def season_for(fares: FareStore, family: str, zone_a: str, zone_b: str, day: date) -> str | None:
    """Season of a travel date."""
    group = season_group_for(fares, family, zone_a, zone_b)
    return fares.season_on(family, group, day) if group else None


def get_required_miles(
    fares: FareStore,
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
    partner_program: str | None = None,
    total_distance_miles: int | None = None,
) -> MilesResult:
    base: dict[str, Any] = {
        "airline": airline,
        "award_type": award_type,
        "trip_type": trip_type,
        "cabin": cabin,
    }
    if award_type == "partner" and airline == "NH":
        return _nh_partner(
            fares, base, origin, destination, cabin, trip_type, booking_date or today_jst()
        )
    if award_type == "partner" and airline == "JL":
        return _jl_partner(
            fares, base, origin, destination, cabin, partner_program, total_distance_miles
        )
    family = SUPPORTED_FAMILIES.get((airline, award_type))
    if family is None:
        return MilesResult(
            "unsupported",
            f"{airline} の {award_type} 特典の必要マイルチャートは未収録です",
            **base,
        )
    if family in ROUTE_FAMILIES:
        return _route_required_miles(
            fares,
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

    o, o_cands = _endpoint(fares, family, origin)
    d, d_cands = _endpoint(fares, family, destination)
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
    charts = fares.charts_for(family, booking, trip_type)
    if not charts:
        res.status = "not_found"
        res.message = f"予約・発券日 {booking} に適用されるチャートが収録されていません。"
        return res
    chart = charts[0]
    res.price_kind = chart.price_kind
    res.chart = {
        "chart_id": chart.chart_id,
        "booking_from": chart.booking_from.isoformat(),
        "source_url": chart.source_url,
        "fetched_at": chart.fetched_at.astimezone(JST).isoformat(),
    }

    pair = {(o.zone, d.zone), (d.zone, o.zone)}
    rows: list[dict[str, Any]] = [
        {"season": r.season, "miles": r.miles, "source_ref": r.source_ref}
        for r in sorted(fares.family(family).rates, key=lambda r: SEASON_ORDER[r.season])
        if r.chart_id == chart.chart_id
        and r.cabin == cabin
        and (r.origin_zone, r.dest_zone) in pair
    ]
    if not rows:
        res.status = "not_found"
        res.message = f"Zone{o.zone}–Zone{d.zone} の {cabin} クラスは特典航空券の設定がありません（設定なし）。"
        return res
    by_season = {r["season"]: r["miles"] for r in rows}
    res.results = [{"season": r["season"], "miles": r["miles"]} for r in rows]
    res.chart["source_refs"] = [r["source_ref"] for r in rows]
    res.caveats = list(STANDARD_CAVEATS)

    if outbound_date:
        s_out = season_for(fares, family, o.zone, d.zone, outbound_date)
        res.seasons_for_dates["outbound"] = s_out
        if s_out is None:
            res.caveats.append(f"{outbound_date} のシーズンは収録カレンダー外のため判定できません")
        if trip_type == "OW" and s_out:
            res.total_miles = by_season[s_out]
            res.calculation = f"片道 {s_out} シーズン = {res.total_miles:,}"
        elif trip_type == "RT" and return_date:
            s_ret = season_for(fares, family, o.zone, d.zone, return_date)
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


def _route_required_miles(
    fares: FareStore,
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
    charts = {c.chart_id: c for c in fares.charts_for(family, booking)}
    jp_tokens, fx_tokens = set(_route_tokens(jp_q, jp_city)), set(_route_tokens(fx_q, fx_city))
    rows: list[dict[str, Any]] = [
        {
            "chart_id": r.chart_id,
            "price_kind": charts[r.chart_id].price_kind,
            "booking_from": charts[r.chart_id].booking_from,
            "source_url": charts[r.chart_id].source_url,
            "fetched_at": charts[r.chart_id].fetched_at,
            "route_label": r.route_label,
            "flight_numbers": list(r.flight_numbers) or None,
            "season": r.season,
            "miles": r.miles,
            "source_ref": r.source_ref,
        }
        for r in fares.family(family).rates
        if r.chart_id in charts
        and r.cabin == cabin
        and r.origin_match & jp_tokens
        and r.dest_match & fx_tokens
    ]
    # floor/fixed before ceiling, then by route and season (the order answers list them in)
    rows.sort(key=lambda r: (r["route_label"] or "", SEASON_ORDER[r["season"]]))
    rows.sort(key=lambda r: r["price_kind"], reverse=True)
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
            seasons = [fares.season_on(family, "F", d) if d else None for d in dates]
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


# ---------------------------------------------------------------- partner awards

PARTNER_CAVEATS = [
    "提携航空会社特典航空券は ANA 便とは別のチャート。シーズンによる変動はない",
    "提携航空会社の空席は別途確認が必要",
]
ZONE_1_LABELS = {
    "1A": "Zone 1-A（国際線往復2区間のみ、または日本国内のみで乗り継ぎ）",
    "1B": "Zone 1-B（Zone 1-A の設定がない場合、または国際線で乗り継ぐ旅程）",
}


def _zone_endpoint(
    fares: FareStore, family: str, query: str
) -> tuple[Endpoint, list[City], str | None]:
    """City first; a country or region name ("中国", "欧洲") works for zone charts."""
    cities = resolve_city(query)
    ep = Endpoint(input=query)
    if len(cities) > 1:
        return ep, cities, None
    if cities:
        c = cities[0]
        ep.city_code, ep.name_ja, ep.airports = c.city_code, c.name_ja, list(c.airports)
        zone = fares.zone_for(family, list(c.airports), c.city_code, c.country)
        ep.zone = "1A" if zone == "1A" else zone
        return ep, cities, None if zone else f"{c.name_ja} はゾーン表で判定できません。"
    area = resolve_area(query)
    if area is None:
        return ep, [], f"「{query}」を都市・国・地域として認識できません。"
    ep.name_ja = area.name_ja
    if area.kind == "country":
        ep.zone = fares.zone_for(family, [], "", area.code)
    else:
        ep.zone = fares.zone_for(family, [], "", "", area.code)
    msg = None if ep.zone else f"{area.name_ja} は複数ゾーンにまたがるため、都市名が必要です。"
    return ep, [], msg


def _nh_partner(
    fares: FareStore,
    base: dict[str, Any],
    origin: str,
    destination: str,
    cabin: str,
    trip_type: str,
    booking: date,
) -> MilesResult:
    o, o_cands, o_err = _zone_endpoint(fares, NH_PARTNER, origin)
    d, d_cands, d_err = _zone_endpoint(fares, NH_PARTNER, destination)
    res = MilesResult("ok", origin=o, destination=d, **base)
    for label, cands in (("origin", o_cands), ("destination", d_cands)):
        if len(cands) > 1:
            res.status = "needs_clarification"
            res.candidates[label] = [f"{c.name_ja} ({c.city_code})" for c in cands]
    if res.status != "ok":
        return res
    if o_err or d_err:
        res.status, res.message = "not_found", (o_err or "") + (d_err or "")
        return res
    assert o.zone is not None and d.zone is not None
    if o.zone == d.zone == "1A":
        res.status = "not_found"
        res.message = "日本国内のみの旅程は提携航空会社特典航空券（国際線）の対象外です。"
        return res
    charts = fares.charts_for(NH_PARTNER, booking, trip_type)
    if not charts:
        res.status = "not_found"
        res.message = (
            f"予約・発券日 {booking} に適用される提携航空会社特典の"
            f"{'片道' if trip_type == 'OW' else '往復'}チャートは収録されていません。"
        )
        return res
    chart = charts[0]
    res.price_kind = chart.price_kind
    res.chart = {
        "chart_id": chart.chart_id,
        "booking_from": chart.booking_from.isoformat(),
        "source_url": chart.source_url,
        "fetched_at": chart.fetched_at.astimezone(JST).isoformat(),
    }
    abroad = d.zone if o.zone == "1A" else o.zone
    pairs: list[tuple[str | None, set[tuple[str, str]]]]
    if "1A" in (o.zone, d.zone):
        pairs = [(z, {(z, abroad), (abroad, z)}) for z in ("1A", "1B")]
    else:
        pairs = [(None, {(o.zone, d.zone), (d.zone, o.zone)})]
    rows: list[dict[str, Any]] = []
    for japan_zone, pair in pairs:
        for r in fares.family(NH_PARTNER).rates:
            if (
                r.chart_id == chart.chart_id
                and r.cabin == cabin
                and (r.origin_zone, r.dest_zone) in pair
            ):
                rows.append(
                    {
                        **({"variant": ZONE_1_LABELS[japan_zone]} if japan_zone else {}),
                        "season": "ANY",
                        "miles": r.miles,
                        "source_ref": r.source_ref,
                    }
                )
    if not rows:
        res.status = "not_found"
        res.message = (
            f"この区間の {cabin} クラスは提携航空会社特典航空券の設定がありません（設定なし）。"
        )
        return res
    res.results = [{k: v for k, v in r.items() if k != "source_ref"} for r in rows]
    res.chart["source_refs"] = [r["source_ref"] for r in rows]
    res.caveats = list(STANDARD_CAVEATS) + PARTNER_CAVEATS
    if len(rows) == 1:
        res.total_miles = rows[0]["miles"]
        if "1A" in (o.zone, d.zone):
            res.caveats.append("Zone 1-A の設定がないため Zone 1-B が適用される")
    else:
        res.caveats.append("日本発着は旅程により Zone 1-A と Zone 1-B のどちらかが適用される")
    if trip_type == "OW":
        res.caveats.append(
            "片道の提携航空会社特典航空券は、必要マイルチャートでは2025年6月24日以降の予約・発券分から"
            "利用可能と記載（ご利用条件ページには「片道ではご利用になれません」の記載が残っている）"
        )
    return res


def _jl_partner(
    fares: FareStore,
    base: dict[str, Any],
    origin: str,
    destination: str,
    cabin: str,
    program: str | None,
    total_distance: int | None,
) -> MilesResult:
    res = MilesResult("ok", origin=Endpoint(origin), destination=Endpoint(destination), **base)
    programs = [program] if program in JL_PARTNER else list(JL_PARTNER)
    estimate = (
        _estimate_distance(origin, destination, base["trip_type"])
        if total_distance is None
        else None
    )
    lookup = total_distance if total_distance is not None else (estimate or {}).get("miles")
    rows: list[dict[str, Any]] = []
    for prog in programs:
        family = fares.family(JL_PARTNER[prog])
        chart = next(iter(family.charts.values()))
        for r in sorted(
            (r for r in family.rates if r.cabin == cabin), key=lambda r: r.distance_min or 0
        ):
            assert r.distance_min is not None and r.distance_max is not None
            if lookup is not None and not _band_matches(
                r.distance_min, r.distance_max, lookup, estimate
            ):
                continue
            rows.append(
                {
                    "variant": f"{prog}: 総旅程距離 {r.distance_min:,}–{r.distance_max:,} マイル",
                    "season": "ANY",
                    "miles": r.miles,
                    "source_url": chart.source_url,
                }
            )
    if not rows:
        res.status = "not_found"
        res.message = (
            f"{cabin} クラスは該当する JAL 提携社特典のチャートに設定がありません"
            if total_distance is None
            else f"総旅程距離 {total_distance:,} マイルは収録チャートの範囲外です"
        )
        return res
    res.results = [{k: v for k, v in r.items() if k != "source_url"} for r in rows]
    first = fares.family(JL_PARTNER[programs[0]])
    chart = next(iter(first.charts.values()))
    res.price_kind = "fixed"
    res.chart = {
        "chart_ids": [next(iter(fares.family(JL_PARTNER[p]).charts)) for p in programs],
        "booking_from": chart.booking_from.isoformat(),
        "source_url": chart.source_url,
        "source_urls": sorted({r["source_url"] for r in rows}),
        "fetched_at": chart.fetched_at.astimezone(JST).isoformat(),
    }
    res.caveats = [
        *list(STANDARD_CAVEATS),
        "JAL の提携社特典は距離制: 必要マイル数は旅程全体の各区間の実マイル数の合計（総旅程距離）で決まる",
        "ワンワールド加盟社の便はワンワールド特典航空券、それ以外の提携社は JMB提携航空会社特典航空券のチャート",
    ]
    if estimate:
        res.calculation = f"推定総旅程距離 {estimate['miles']:,} マイル（{estimate['basis']}）"
        res.caveats.append(
            "総旅程距離は直行便の大圏距離からの推定値。実際の区間マイル数や乗り継ぎ経路で変わり、"
            "距離帯の境界に近い場合は隣の距離帯も示している"
        )
    elif total_distance is None:
        res.message = "総旅程距離が未指定のため、距離帯ごとの表を返す。経路（乗り継ぎを含む）か総旅程距離が分かれば1つに絞れる"
    if lookup is not None and len(rows) == 1:
        res.total_miles = rows[0]["miles"]
    return res


BOUNDARY_MARGIN = 0.03  # an estimate this close to a band edge also returns the next band


def _estimate_distance(origin: str, destination: str, trip_type: str) -> dict[str, Any] | None:
    """Nonstop great-circle distance between the main airports (x2 for a round trip)."""
    ends = []
    for q in (origin, destination):
        cities = resolve_city(q)
        if len(cities) != 1:
            return None
        hint = airport_hint(q)
        ends.append(hint if hint and hint in cities[0].airports else cities[0].airports[0])
    one_way = great_circle_miles(ends[0], ends[1])
    if not one_way:
        return None
    legs = 2 if trip_type == "RT" else 1
    basis = f"{ends[0]}–{ends[1]} 大圏距離 {one_way:,} マイル" + (
        " × 2（往復）" if legs == 2 else ""
    )
    return {"miles": one_way * legs, "basis": basis}


def _band_matches(lo: int, hi: int, miles: int, estimate: dict[str, Any] | None) -> bool:
    if estimate is None:
        return lo <= miles <= hi
    margin = miles * BOUNDARY_MARGIN
    return lo - margin <= miles <= hi + margin
