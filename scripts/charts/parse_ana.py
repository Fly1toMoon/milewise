"""Parse a saved ANA award-chart page into committed CSVs.

    uv run python scripts/charts/parse_ana.py raw/nh/ana-intl-award-chart_<ts>.html

Writes data/charts/NH-INTL-OWN/{manifest.yaml,zones.csv,zone_members.csv,seasons.csv,rates.csv}.
The raw HTML stays out of git; the manifest records its URL, fetch time and SHA-256.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

from milewise.charts.ana import (
    BOOKING_FROM,
    CHART_FAMILY,
    PARSER_VERSION,
    SOURCE_URL,
    parse_ana_award_chart,
)
from milewise.locations import load_cities

OUT = Path("data/charts") / CHART_FAMILY


def fetched_at_from_name(path: Path) -> str:
    m = re.search(r"(\d{8}T\d{6})", path.name)
    if not m:
        raise SystemExit("raw file name must contain a timestamp like 20261017T003345")
    ts = datetime.strptime(m.group(1), "%Y%m%dT%H%M%S")
    return ts.replace(tzinfo=datetime.now().astimezone().tzinfo).astimezone(UTC).isoformat()


def write_csv(path: Path, header: list[str], rows: list[list[object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("raw_html", type=Path)
    a = ap.parse_args()

    raw = a.raw_html.read_bytes()
    chart = parse_ana_award_chart(raw.decode("utf-8"))
    if chart.warnings:
        for w in chart.warnings:
            print(f"warning: {w}", file=sys.stderr)
        print("refusing to write: resolve parser warnings first", file=sys.stderr)
        return 1

    # Map zone cities (Japanese names on the page) to IATA city codes.
    by_ja = {c.name_ja: c for c in load_cities()}
    members: list[list[object]] = []
    for z in chart.zones:
        if z.code == "1":
            members.append([CHART_FAMILY, z.code, "country", "JP"])
            continue
        for city in z.cities_ja:
            if city not in by_ja:
                print(f"unknown city on zone table: {city} (add it to data/locations/cities.csv)")
                return 1
            members.append([CHART_FAMILY, z.code, "city", by_ja[city].city_code])

    group_of = {z: g for g, zs in chart.season_groups.items() for z in zs if z != "1"}
    OUT.mkdir(parents=True, exist_ok=True)
    write_csv(
        OUT / "zones.csv",
        ["chart_family", "zone_code", "name_ja", "season_group"],
        [[CHART_FAMILY, z.code, z.name_ja, group_of.get(z.code, "")] for z in chart.zones],
    )
    write_csv(
        OUT / "zone_members.csv",
        ["chart_family", "zone_code", "match_kind", "match_value"],
        members,
    )
    write_csv(
        OUT / "seasons.csv",
        ["chart_family", "season_group", "season", "date_from", "date_to"],
        [
            [CHART_FAMILY, s.season_group, s.season, s.date_from.isoformat(), s.date_to.isoformat()]
            for s in sorted(chart.seasons, key=lambda s: (s.season_group, s.date_from))
        ],
    )
    chart_ids = {t: f"{CHART_FAMILY}-{t}@{BOOKING_FROM.isoformat()}" for t in ("RT", "OW")}
    write_csv(
        OUT / "rates.csv",
        ["chart_id", "origin_zone", "dest_zone", "cabin", "season", "miles", "source_ref"],
        [
            [chart_ids[r.trip_type], r.zone_a, r.zone_b, r.cabin, r.season, r.miles, r.source_ref]
            for r in chart.rates
        ],
    )
    manifest = {
        "chart_family": CHART_FAMILY,
        "airline": "NH",
        "award_type": "own_intl",
        "pricing_basis": "zone_pair",
        "price_kind": "fixed",
        "source_url": SOURCE_URL,
        "fetched_at": fetched_at_from_name(a.raw_html),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "parser_version": PARSER_VERSION,
        "charts": [
            {
                "chart_id": chart_ids["RT"],
                "trip_type": "RT",
                "booking_from": BOOKING_FROM.isoformat(),
            },
            {
                "chart_id": chart_ids["OW"],
                "trip_type": "OW",
                "booking_from": BOOKING_FROM.isoformat(),
            },
        ],
        "notes": [
            "Rates are symmetric (発着): a zone pair applies in both directions.",
            "Where the page shows 改定前/改定後, only 改定後 (bookings from 2025-06-24) is kept.",
            "Round trip with different outbound/return seasons: half of each season's RT value, summed.",
            "Season calendars are defined only for itineraries with Japan (Zone 1) as one end.",
        ],
        "counts": {
            "zones": len(chart.zones),
            "season_ranges": len(chart.seasons),
            "rates_rt": sum(r.trip_type == "RT" for r in chart.rates),
            "rates_ow": sum(r.trip_type == "OW" for r in chart.rates),
        },
    }
    (OUT / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    print(f"wrote {OUT}: {manifest['counts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
