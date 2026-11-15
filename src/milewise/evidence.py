"""What this turn's tools returned: the only facts an answer may state (design.md §4.13).

Every tool result the model can cite gets an evidence id (C1 for charts and seasons, D1 for
rule excerpts) and adds the figures and URLs it backs. The answer is verified against this
alone (milewise.verify), so nothing from memory or from earlier turns counts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from milewise.tools.miles import itinerary_facts

OFFICIAL_URLS = {
    "ANA international award chart (zones, seasons, required miles)": "https://www.ana.co.jp/ja/jp/guide/amc/award/international/terms/",
    "JAL international award required miles": "https://www.jal.co.jp/jp/ja/jalmile/use/jal/inter/routemiles.html",
}

# ASCII URL characters only, so trailing CJK punctuation ("…/refund/（取得日", "/，") is not
# swallowed into the URL. Shared with the eval grader.
URL_RE = re.compile(r"https?://[A-Za-z0-9\-._~:/?#@!$&*+=%]+")
ANY_NUMBER = re.compile(r"\d{1,3}(?:[,，]\d{3})+|\d+")
# Models sometimes write full-width marks (【D1】, ［D1］; seen with GLM-5): the answer is
# normalised to [D1] before any check, so they are neither missed nor shown raw.
_ANY_MARK = re.compile(r"[\[［【]\s*([CD]\d+)\s*[\]］】]")


def normalize_marks(text: str) -> str:
    return _ANY_MARK.sub(r"[\1]", text)


def as_int(number: str) -> int:
    return int(re.sub(r"[,，]", "", number))


@dataclass
class Evidence:
    items: dict[str, dict[str, Any]] = field(default_factory=dict)
    allowed_miles: set[int] = field(default_factory=set)
    allowed_urls: set[str] = field(default_factory=lambda: set(OFFICIAL_URLS.values()))
    # Tokens spent inside tools (the reranker), so per-request cost is complete.
    tool_usage: dict[str, int] = field(
        default_factory=lambda: {"input_tokens": 0, "output_tokens": 0}
    )
    # itinerary_facts flags of the trips in this conversation (this turn's tools and the
    # itineraries earlier answers used): they decide which rule notes apply (§4.13)
    itinerary_flags: set[str] = field(default_factory=set)
    # the latest itinerary a tool was given in this turn, returned so the client can send it
    # back with this answer as history
    itinerary: list[list[str]] | None = None

    def add_itinerary(self, runs: list[list[str]] | None) -> None:
        if not runs:
            return
        facts = itinerary_facts(runs)
        if "unresolved" not in facts:
            self.itinerary_flags.update(facts["flags"])
            self.itinerary = runs

    def add_chart(self, result: dict[str, Any]) -> str:
        eid = f"C{sum(k.startswith('C') for k in self.items) + 1}"
        self.items[eid] = result
        for r in result.get("results", []):
            self.allowed_miles.update({r["miles"], r["miles"] // 2})
            # distance-band labels ("総旅程距離 10,001–12,000 マイル") are citable too
            self.allowed_miles.update(as_int(n) for n in ANY_NUMBER.findall(r.get("variant", "")))
        if result.get("total_miles"):
            self.allowed_miles.add(result["total_miles"])
        # figures inside the tool's own calculation text (e.g. an estimated distance)
        self.allowed_miles.update(
            as_int(n) for n in ANY_NUMBER.findall(result.get("calculation") or "")
        )
        self.allowed_miles.update(result.get("totals", {}).values())
        chart = result.get("chart") or {}
        # JAL partner awards cite two charts (oneworld and JMB partners)
        self.allowed_urls.update(
            u for u in [chart.get("source_url"), *chart.get("source_urls", [])] if u
        )
        return eid

    def add_doc(self, chunk: dict[str, Any]) -> str:
        eid = f"D{sum(k.startswith('D') for k in self.items) + 1}"
        self.items[eid] = chunk
        self.allowed_urls.add(chunk["url"])
        # Figures quoted from a rule (e.g. a 3,000-mile fee) are backed by the document.
        self.allowed_miles.update(as_int(n) for n in ANY_NUMBER.findall(chunk["text"]))
        return eid
