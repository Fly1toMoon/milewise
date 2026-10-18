"""Parse JAL's distance-based partner award charts into committed CSVs.

    uv run python scripts/charts/parse_jal_partner.py \
        raw/jl/jal-ow-award-chart_<ts>.html raw/jl/jal-pjmb-award-chart_<ts>.html

Writes data/charts/JL-INTL-OW/ (oneworld) and data/charts/JL-INTL-JMBP/ (JMB partners).
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

from milewise.charts.jal_partner import PARSER_VERSION, PROGRAMS, chart_id, parse

FILES = {"jal-ow-award-chart": "oneworld", "jal-pjmb-award-chart": "jmb_partner"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("raw_html", type=Path, nargs="+")
    a = ap.parse_args()
    for path in a.raw_html:
        program = next(p for prefix, p in FILES.items() if path.name.startswith(prefix + "_"))
        meta = PROGRAMS[program]
        res = parse(program, path.read_text(encoding="utf-8"))
        m = re.search(r"(\d{8}T\d{6})", path.name)
        if not m:
            raise SystemExit("raw file name must contain a timestamp")
        fetched = datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").astimezone().astimezone(UTC)
        out = Path("data/charts") / meta["family"]
        out.mkdir(parents=True, exist_ok=True)
        with (out / "rates.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(res.rates[0]), lineterminator="\n")
            w.writeheader()
            w.writerows(res.rates)
        manifest = {
            "chart_family": meta["family"],
            "airline": "JL",
            "award_type": "partner_intl",
            "program": program,
            "name_ja": meta["name_ja"],
            "pricing_basis": "distance",
            "price_kind": "fixed",
            "source_url": meta["url"],
            "fetched_at": fetched.isoformat(),
            "source_sha256": res.source_sha256,
            "parser_version": PARSER_VERSION,
            "charts": [
                {"chart_id": chart_id(program), "trip_type": "ITIN", "booking_from": "2026-10-17"}
            ],
            "notes": [
                "Distance-based: 総旅程距離 = sum of the actual segment miles of the whole"
                " itinerary; one award covers it (round trip or one way).",
                "Japan-resident chart (日本にお住まいの方).",
                "The page states no effective date; booking_from is the fetch date.",
            ],
            "counts": {
                c: sum(r["cabin"] == c for r in res.rates)
                for c in dict.fromkeys(r["cabin"] for r in res.rates)
            },
        }
        (out / "manifest.yaml").write_text(
            yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        print(f"wrote {out}: {manifest['counts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
