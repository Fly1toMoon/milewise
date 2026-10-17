"""Load committed chart CSVs (data/charts/<family>/) into the fare schema.

Idempotent: each family is replaced wholesale inside one transaction, so re-running
after `terraform apply` or after a chart update always converges to the files.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import yaml

from milewise.config import data_dir
from milewise.db import Db


def _csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_family(db: Db, family_dir: Path) -> dict[str, int]:
    m: dict[str, Any] = yaml.safe_load((family_dir / "manifest.yaml").read_text(encoding="utf-8"))
    family = m["chart_family"]
    zones = _csv(family_dir / "zones.csv")
    members = _csv(family_dir / "zone_members.csv")
    seasons = _csv(family_dir / "seasons.csv")
    rates = _csv(family_dir / "rates.csv")
    chart_ids = [c["chart_id"] for c in m["charts"]]

    with db.transaction():
        db.execute("DELETE FROM fare.award_charts WHERE chart_family = :f", {"f": family})
        db.execute("DELETE FROM fare.season_calendar WHERE chart_family = :f", {"f": family})
        db.execute("DELETE FROM fare.zone_members WHERE chart_family = :f", {"f": family})
        db.execute("DELETE FROM fare.zones WHERE chart_family = :f", {"f": family})

        db.execute_many(
            "INSERT INTO fare.award_charts (chart_id, chart_family, airline, award_type, trip_type,"
            " pricing_basis, price_kind, booking_from, booking_to, source_url, fetched_at,"
            " source_sha256, parser_version) VALUES (:chart_id, :family, :airline, :award_type,"
            " :trip_type, :pricing_basis, :price_kind, CAST(:booking_from AS date),"
            " CAST(:booking_to AS date), :source_url, CAST(:fetched_at AS timestamptz),"
            " :source_sha256, :parser_version)",
            [
                {
                    "chart_id": c["chart_id"],
                    "family": family,
                    "airline": m["airline"],
                    "award_type": m["award_type"],
                    "trip_type": c["trip_type"],
                    "pricing_basis": m["pricing_basis"],
                    "price_kind": m["price_kind"],
                    "booking_from": c["booking_from"],
                    "booking_to": c.get("booking_to"),
                    "source_url": m["source_url"],
                    "fetched_at": m["fetched_at"],
                    "source_sha256": m["source_sha256"],
                    "parser_version": m["parser_version"],
                }
                for c in m["charts"]
            ],
        )
        db.execute_many(
            "INSERT INTO fare.zones VALUES (:chart_family, :zone_code, :name_ja,"
            " NULLIF(:season_group, ''))",
            zones,
        )
        db.execute_many(
            "INSERT INTO fare.zone_members VALUES (:chart_family, :zone_code, :match_kind, :match_value)",
            members,
        )
        db.execute_many(
            "INSERT INTO fare.season_calendar VALUES (:chart_family, :season_group, :season,"
            " CAST(:date_from AS date), CAST(:date_to AS date))",
            seasons,
        )
        db.execute_many(
            "INSERT INTO fare.award_rates (chart_id, origin_zone, dest_zone, cabin, season, miles,"
            " source_ref) VALUES (:chart_id, :origin_zone, :dest_zone, :cabin, :season,"
            " CAST(:miles AS int), :source_ref)",
            [r for r in rates if r["chart_id"] in chart_ids],
        )
    return {
        "charts": len(chart_ids),
        "zones": len(zones),
        "seasons": len(seasons),
        "rates": len(rates),
    }


def load_all(db: Db, charts_dir: Path | None = None) -> dict[str, dict[str, int]]:
    charts_dir = charts_dir or data_dir() / "charts"
    return {
        d.name: load_family(db, d)
        for d in sorted(charts_dir.iterdir())
        if (d / "manifest.yaml").exists()
    }
