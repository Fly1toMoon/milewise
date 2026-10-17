"""Import browser-saved page bundles into the raw/ naming scheme the pipeline uses.

    uv run python scripts/import_raw_bundle.py              # every data/raw/*/manifest.json
    uv run python scripts/import_raw_bundle.py --dry-run

A bundle is a directory with manifest.json ({saved_at, pages: [{id, file, url}]}) and the
saved pages. Each page that data/sources.yaml uses (kind rules or chart) is copied to
raw/<airline>/<source id>_<saved_at in JST>.html; excluded pages are skipped. Pages are
matched by bundle_id, id, then final URL. Both data/raw/ and raw/ are gitignored and kept
out of the Docker image: raw pages are never committed or shipped (design.md §5.4).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

JST = timezone(timedelta(hours=9))
USED_KINDS = {"rules", "chart"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundles", type=Path, default=Path("data/raw"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    cfg = yaml.safe_load(Path("data/sources.yaml").read_text(encoding="utf-8"))
    sources = cfg["sources"]
    by_key: dict[str, dict[str, object]] = {}
    for s in sources:
        for key in (s.get("bundle_id"), s["id"], s["url"]):
            if key:
                by_key[str(key)] = s
    excluded = {e["id"] for e in cfg.get("excluded", [])}

    copied = skipped = 0
    unknown: list[str] = []
    for manifest_path in sorted(a.bundles.glob("*/manifest.json")):
        bundle = manifest_path.parent
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(m["saved_at"].replace("Z", "+00:00")).astimezone(JST)
        stamp = ts.strftime("%Y%m%dT%H%M%S")
        for page in m["pages"]:
            src = by_key.get(page["id"]) or by_key.get(page["url"])
            if src is None:
                if page["id"] not in excluded:
                    unknown.append(page["id"])
                continue
            if src["kind"] not in USED_KINDS or not page["file"].endswith(".html"):
                skipped += 1
                continue
            target = Path("raw") / str(src["airline"]).lower() / f"{src['id']}_{stamp}.html"
            if target.exists() and target.read_bytes() == (bundle / page["file"]).read_bytes():
                skipped += 1
                continue
            print(
                f"{'would copy' if a.dry_run else 'copy'}  {bundle.name}/{page['file']} -> {target}"
            )
            if not a.dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(bundle / page["file"], target)
            copied += 1
    print(f"{copied} copied, {skipped} skipped (not used in v1 or unchanged)")
    if unknown:
        print(f"not in data/sources.yaml (add to sources or excluded): {unknown}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
