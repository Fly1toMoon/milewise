"""Polite fetcher for the `fetch: http` pages in data/sources.yaml.

    uv run python scripts/fetch_sources.py            # all http sources
    uv run python scripts/fetch_sources.py --only ana-intl-award-refund

Saves raw/<airline>/<id>_<YYYYmmddTHHMMSS>.html (gitignored). One request per page,
a fixed delay between requests, an identifying User-Agent, and robots.txt is checked
before each request. Pages marked `fetch: manual` are skipped with a reminder.
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.robotparser
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import yaml

DELAY_S = 5.0
RAW = Path("raw")


def allowed(url: str, ua: str, cache: dict[str, urllib.robotparser.RobotFileParser]) -> bool:
    origin = "{0.scheme}://{0.netloc}".format(urlsplit(url))
    if origin not in cache:
        rp = urllib.robotparser.RobotFileParser()
        r = httpx.get(f"{origin}/robots.txt", headers={"User-Agent": ua}, timeout=30)
        rp.parse(r.text.splitlines() if r.status_code == 200 else [])
        cache[origin] = rp
    return cache[origin].can_fetch(ua, url)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", type=Path, default=Path("data/sources.yaml"))
    ap.add_argument("--only", action="append", help="source id (repeatable)")
    a = ap.parse_args()

    cfg = yaml.safe_load(a.sources.read_text(encoding="utf-8"))
    ua = cfg["user_agent"]
    robots: dict[str, urllib.robotparser.RobotFileParser] = {}
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    failures = 0

    for i, src in enumerate(s for s in cfg["sources"] if not a.only or s["id"] in a.only):
        if src["fetch"] != "http":
            print(f"skip   {src['id']}: manual save required ({src['url']})")
            continue
        if i:
            time.sleep(DELAY_S)
        if not allowed(src["url"], ua, robots):
            print(f"skip   {src['id']}: disallowed by robots.txt")
            continue
        r = httpx.get(src["url"], headers={"User-Agent": ua}, timeout=60, follow_redirects=True)
        if r.status_code != 200:
            failures += 1
            print(f"FAIL   {src['id']}: HTTP {r.status_code}")
            continue
        out = RAW / src["airline"].lower() / f"{src['id']}_{ts}.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(r.content)
        print(f"saved  {src['id']}: {len(r.content):,} B -> {out}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
