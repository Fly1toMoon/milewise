"""Extract coordinates of the airports in data/locations/cities.csv from OurAirports.

    curl -sSfLo /tmp/ourairports/airports.csv https://davidmegginson.github.io/ourairports-data/airports.csv
    uv run python scripts/locations/extract_airports.py /tmp/ourairports/airports.csv

OurAirports data is public domain (https://ourairports.com/data/). Only the few dozen rows
Milewise needs are committed, as data/locations/airports.csv.
"""

from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path

from milewise.locations import load_cities

OUT = Path("data/locations/airports.csv")


def main() -> int:
    src = Path(sys.argv[1])
    wanted = {a for c in load_cities() for a in c.airports}
    rows = {}
    with src.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            code = r["iata_code"]
            if code in wanted and r["type"] in ("large_airport", "medium_airport", "small_airport"):
                rows[code] = {
                    "iata": code,
                    "name": r["name"],
                    "iso_country": r["iso_country"],
                    "lat": f"{float(r['latitude_deg']):.4f}",
                    "lon": f"{float(r['longitude_deg']):.4f}",
                }
    missing = sorted(wanted - rows.keys())
    with OUT.open("w", newline="", encoding="utf-8") as f:
        f.write(f"# OurAirports (public domain), extracted {date.today().isoformat()}\n")
        w = csv.DictWriter(
            f, fieldnames=["iata", "name", "iso_country", "lat", "lon"], lineterminator="\n"
        )
        w.writeheader()
        w.writerows(rows[k] for k in sorted(rows))
    print(f"wrote {OUT}: {len(rows)} airports; missing: {missing or 'none'}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
