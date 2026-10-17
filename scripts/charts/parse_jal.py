"""Parse a saved JAL award-chart page into committed CSVs.

    uv run python scripts/charts/parse_jal.py raw/jl/jal-intl-award-chart_<ts>.html

Writes data/charts/JL-INTL-OWN/{manifest.yaml,seasons.csv,rates.csv}. Three charts, all
one-way: basic miles (floor), first class (fixed, by season), PLUS maximum (ceiling).
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

from milewise.charts.jal import (
    CHART_FAMILY,
    PARSER_VERSION,
    ROUTES,
    SOURCE_URL,
    parse_jal_award_chart,
)

OUT = Path("data/charts") / CHART_FAMILY
# JAL does not state an effective date on the page; its "更新日" is used (design.md §5.2).
BOOKING_FROM = "2026-04-16"
CHARTS = {
    "base": ("BASE", "floor"),
    "first": ("F", "fixed"),
    "plus_max": ("PLUSMAX", "ceiling"),
}


def chart_id(block: str) -> str:
    return f"{CHART_FAMILY}-OW-{CHARTS[block][0]}@{BOOKING_FROM}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("raw_html", type=Path)
    a = ap.parse_args()
    raw = a.raw_html.read_bytes()
    chart = parse_jal_award_chart(raw.decode("utf-8"))
    if chart.warnings:
        for w in chart.warnings:
            print(f"warning: {w}", file=sys.stderr)
        print("refusing to write: resolve parser warnings first", file=sys.stderr)
        return 1

    m = re.search(r"(\d{8}T\d{6})", a.raw_html.name)
    if not m:
        raise SystemExit("raw file name must contain a timestamp like 20261017T095917")
    fetched = datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").astimezone().astimezone(UTC)

    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "seasons.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["chart_family", "season_group", "season", "date_from", "date_to"])
        for s in sorted(chart.seasons, key=lambda s: s.date_from):
            w.writerow(
                [CHART_FAMILY, "F", s.season, s.date_from.isoformat(), s.date_to.isoformat()]
            )
    with (OUT / "rates.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(
            [
                "chart_id",
                "origin_match",
                "dest_match",
                "flight_numbers",
                "route_label",
                "cabin",
                "season",
                "miles",
                "source_ref",
            ]
        )
        for r in chart.rates:
            origin, dest, flights = ROUTES[r.label]
            w.writerow(
                [
                    chart_id(r.block),
                    "|".join(origin),
                    "|".join(dest),
                    "|".join(flights or []),
                    r.label,
                    r.cabin,
                    r.season,
                    r.miles,
                    r.source_ref,
                ]
            )
    manifest = {
        "chart_family": CHART_FAMILY,
        "airline": "JL",
        "award_type": "own_intl",
        "pricing_basis": "route",
        "source_url": SOURCE_URL,
        "fetched_at": fetched.isoformat(),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "parser_version": PARSER_VERSION,
        "charts": [
            {
                "chart_id": chart_id(b),
                "trip_type": "OW",
                "booking_from": BOOKING_FROM,
                "price_kind": kind,
                "block": b,
            }
            for b, (_, kind) in CHARTS.items()
        ],
        "notes": [
            "One-way, Japan origin/destination only (日本発着（片道）). Round trip = sum of both legs.",
            "BASE (Y/PY/C) are 基本マイル数: the minimum; with 特典航空券PLUS the price is higher, up to PLUSMAX.",
            "First class has no PLUS; it is priced by its own L/R/H season calendar (season_group F).",
            "booking_from is the page's 更新日; the page states no effective date.",
            *chart.notes,
        ],
        "counts": {b: sum(r.block == b for r in chart.rates) for b in CHARTS}
        | {"season_ranges": len(chart.seasons)},
    }
    (OUT / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    print(f"wrote {OUT}: {manifest['counts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
