"""Parse ANA's partner-airline award chart page into committed CSVs.

    uv run python scripts/charts/parse_ana_partner.py raw/nh/ana-partner-award-chart_<ts>.html

Writes data/charts/NH-INTL-PARTNER/{manifest.yaml,zones.csv,zone_members.csv,rates.csv}.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

from milewise.charts.ana_partner import (
    BOOKING_FROM,
    CHART_IDS,
    FAMILY,
    PARSER_VERSION,
    SOURCE_URL,
    parse,
)

OUT = Path("data/charts") / FAMILY


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("raw_html", type=Path)
    a = ap.parse_args()
    res = parse(a.raw_html.read_text(encoding="utf-8"))
    if res.warnings:
        for w in res.warnings:
            print(f"warning: {w}", file=sys.stderr)
        print("refusing to write: resolve parser warnings first", file=sys.stderr)
        return 1
    m = re.search(r"(\d{8}T\d{6})", a.raw_html.name)
    if not m:
        raise SystemExit("raw file name must contain a timestamp like 20261017T100629")
    fetched = datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").astimezone().astimezone(UTC)

    OUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUT / "zones.csv", res.zones)
    write_csv(OUT / "zone_members.csv", res.members)
    write_csv(OUT / "rates.csv", res.rates)
    manifest = {
        "chart_family": FAMILY,
        "airline": "NH",
        "award_type": "partner_intl",
        "pricing_basis": "zone_pair",
        "price_kind": "fixed",
        "source_url": SOURCE_URL,
        "fetched_at": fetched.isoformat(),
        "source_sha256": res.source_sha256,
        "parser_version": PARSER_VERSION,
        "charts": [
            {"chart_id": cid, "trip_type": t, "booking_from": BOOKING_FROM[t]}
            for t, cid in CHART_IDS.items()
        ],
        "notes": [
            "Round-trip chart, and a one-way chart for bookings from 2025-06-24 (the partner usage"
            " page still says 片道ではご利用になれません; the chart page is followed). No seasons;"
            " classes Y / C / F.",
            "Rates are symmetric (⇔) and cover pairs outside Japan too.",
            "Japan: Zone 1-A = only two international segments (+ connections within Japan);"
            " Zone 1-B applies when 1-A has no price or the itinerary has more international segments.",
            "Where the page shows 改定前/改定後, only 改定後 (bookings from 2024-04-18) is kept.",
            f"The page prints {res.n_tables} tables (round trip, then one way); reversed pairs were"
            " checked to agree.",
        ],
        "unmapped_zone_members": res.unmapped,
        "counts": {
            "zones": len(res.zones),
            "members": len(res.members),
            **{t: sum(r["chart_id"] == cid for r in res.rates) for t, cid in CHART_IDS.items()},
        },
    }
    (OUT / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    print(f"wrote {OUT}: {manifest['counts']}, {len(res.unmapped)} unmapped zone members")
    return 0


if __name__ == "__main__":
    sys.exit(main())
