"""Grounding: what this turn must look up, the tools that do it, and the draft that rests on
them (design.md §4.13).

Two guarantees are made here, in code, rather than asked for in the prompt:

* a follow-up to an answer based on rule excerpts searches the rules first (`first_lookup`,
  enforced with the Converse toolChoice by `ForceTool`); a dispute is always such a turn;
* a draft that looked nothing up at all is written once more with RETRY_NOTE.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from typing import Any, Literal, Protocol

from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from milewise.charts.store import FareStore
from milewise.evidence import Evidence
from milewise.locations import resolve_city
from milewise.rag.index import RulesIndex, get_rules_index
from milewise.rag.search import search_rules
from milewise.tools.miles import (
    JAPAN_ZONE,
    JST,
    ROUTE_FAMILIES,
    SUPPORTED_FAMILIES,
    get_required_miles,
    season_group_for,
    today_jst,
)

RETRY_NOTE = """
Your previous attempt at this question answered without calling any tool. Earlier turns and \
your own earlier answers are not evidence. Call the tools that cover this question now \
(search_rules for rules, conditions and itinerary limits; get_required_miles for miles) and \
answer only from what they return. Answer without tools only if the question is outside \
Milewise's scope (rule 5) or is not about award tickets at all.
"""


class Run(Protocol):
    """One agent run (agent.ask): `note` is added to the system prompt, `force` names the
    tool the first model call must make, `hold` keeps streamed text back until a tool call."""

    def __call__(
        self, *, note: str = "", force: str | None = None, hold: bool = False
    ) -> list[Any]: ...


def first_lookup(history: list[dict[str, Any]] | None) -> str | None:
    """The tool this turn must call before anything else, if any.

    The latest earlier answer was based on rule excerpts (the page sends the chunk ids it
    cited): a follow-up to it, a dispute included, looks the rules up again. Asking in the
    prompt was not enough (multi-08: GLM-5 agreed with the user's reading without a lookup
    in 2-3 of 5 runs, also after RETRY_NOTE)."""
    answers = [m for m in history or [] if m.get("role") == "assistant"]
    return "search_rules" if answers and answers[-1].get("evidence") else None


def grounded_draft(run: Run, history: list[dict[str, Any]] | None, evidence: Evidence) -> list[Any]:
    """The first answer, made to rest on this turn's lookups. Its text is held back until
    the first tool call, so a draft written from memory never reaches the page."""
    messages = run(force=first_lookup(history), hold=True)
    if not evidence.items:
        # Answered from memory (seen in follow-ups: invented rules, 2026-10-22).
        messages = run(note=RETRY_NOTE)
    return messages


def conversation_evidence(history: list[dict[str, Any]] | None) -> tuple[Evidence, Any]:
    """Empty evidence for this turn that already knows the trips earlier answers looked up
    (they decide which rule notes apply), and the latest of those trips."""
    evidence = Evidence()
    for m in history or []:
        if m.get("role") == "assistant":
            evidence.add_itinerary(m.get("itinerary"))
    carried, evidence.itinerary = evidence.itinerary, None
    return evidence, carried


class ForceTool(AgentMiddleware):
    """Make the first model call of a run call `name` (Converse toolChoice; GLM-5 honours it,
    checked 2026-11-12)."""

    def __init__(self, tool: str) -> None:
        super().__init__()
        self.tool = tool

    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], Any]) -> Any:
        start = max(
            (i for i, m in enumerate(request.messages) if isinstance(m, HumanMessage)), default=-1
        )
        if not any(isinstance(m, ToolMessage) for m in request.messages[start + 1 :]):
            request = request.override(tool_choice=self.tool)
        return handler(request)


# ---------- the tools ----------


class MilesArgs(BaseModel):
    airline: Literal["NH", "JL"] = Field(description="NH = ANA, JL = JAL")
    origin: str = Field(description="Departure city/airport/IATA code, any language")
    destination: str = Field(description="Arrival city/airport/IATA code, any language")
    cabin: Literal["Y", "PY", "C", "F"] = Field(
        description="Y economy, PY premium economy, C business, F first. When the call is "
        "only for itinerary_facts and no cabin is known, keep the cabin of earlier turns, "
        "else pass Y (and do not report the miles)"
    )
    trip_type: Literal["RT", "OW"] = Field(
        default="OW", description="OW one way (default when the user does not say), RT round trip"
    )
    outbound_date: date | None = Field(default=None, description="Outbound travel date, if known")
    return_date: date | None = Field(default=None, description="Return travel date (RT), if known")
    booking_date: date | None = Field(
        default=None, description="Booking/ticketing date; default today"
    )
    flight_number: str | None = Field(
        default=None, description="Flight number if the user gives one (e.g. JL031)"
    )
    partner_program: Literal["oneworld", "jmb_partner"] | None = Field(
        default=None,
        description="JAL partner awards only: oneworld member airline, or another JMB "
        "partner airline. Omit if unsure (both charts are returned)",
    )
    total_distance_miles: int | None = Field(
        default=None,
        description="JAL partner awards only: total flown distance of the itinerary in "
        "miles, if the user states it",
    )
    itinerary: list[list[str]] | None = Field(
        default=None,
        description="JAL partner awards only: the whole trip in travel order, as lists of "
        "places flown consecutively; a new list starts after a stretch by land (open jaw). "
        'E.g. [["Tokyo","Hong Kong","London","Hong Kong","Tokyo"]]. With trip_type RT and '
        "the same way back, the outbound alone is enough.",
    )
    award_type: Literal["own", "partner"] = Field(
        default="own",
        description="own: flights of the airline itself. partner: an award on a partner "
        "airline (提携航空会社, 伙伴航空, Star Alliance, oneworld), also when the user only "
        "says a partner award without naming the airline",
    )


class RulesArgs(BaseModel):
    question: str = Field(description="What to look up, ideally rewritten in Japanese")
    airline: Literal["NH", "JL"] | None = Field(default=None, description="Limit to one airline")
    award_type: Literal["own", "partner"] | None = Field(
        default=None,
        description="partner when the user asks about partner-airline / oneworld / Star "
        "Alliance awards; own when they ask about awards on the airline's own flights; omit it "
        "if unsure (both are searched). Their rules differ (e.g. JAL partner awards can be "
        "changed after ticketing, JAL's own cannot)",
    )
    itinerary: list[list[str]] | None = Field(
        default=None,
        description="When the question is about a specific trip (also one from earlier turns), "
        "the whole trip in the same form as get_required_miles' itinerary. Used only to pick "
        "Milewise's notes that apply to it",
    )


class SeasonArgs(BaseModel):
    airline: Literal["NH", "JL"]
    destination: str = Field(description="Non-Japan end of the trip (city/airport/IATA code)")
    date_from: date = Field(description="First travel date of the period to look up")
    date_to: date | None = Field(
        default=None,
        description="Last date of the period (inclusive); omit for a single day. "
        "For a month or several months pass the whole span, e.g. 2027-07-01 to 2027-08-31.",
    )


def season_periods(
    fares: FareStore, airline: str, destination: str, date_from: date, date_to: date | None
) -> dict[str, Any]:
    """Season segments (L/R/H) of a travel period, from the airline's calendar."""
    date_to = date_to or date_from
    family = SUPPORTED_FAMILIES.get((airline, "own"))
    cities = resolve_city(destination)
    base = {"airline": airline, "destination": destination}
    if family is None or len(cities) != 1 or date_to < date_from:
        return {"status": "not_found", **base}
    today = today_jst()
    if date_to < today:
        # e.g. "July and August" asked in October resolved to this year's July (seen with Nova)
        return {
            "status": "invalid_dates",
            **base,
            "hint": f"The period ends before today ({today.isoformat()}). If the user gave no "
            "year, a month or season means its next occurrence: call again with next year's "
            "dates. If the user wrote this year explicitly, tell them the period is past.",
        }
    c = cities[0]
    if family in ROUTE_FAMILIES:
        # JAL: only first class is seasonal; Y/PY/C have no seasons (基本マイル数 + PLUS).
        group: str | None = "F"
        note = "JAL のシーズン（L/R/H）はファーストクラスのみに適用。エコノミー・プレミアムエコノミー・ビジネスはシーズンなし"
    else:
        zone = fares.zone_for(family, list(c.airports), c.city_code, c.country)
        group = season_group_for(fares, family, JAPAN_ZONE, zone) if zone else None
        note = ""
    if group is None:
        return {"status": "not_found", **base}
    segments = fares.season_segments(family, group, date_from, date_to)
    covered = sum(
        (date.fromisoformat(g["date_to"]) - date.fromisoformat(g["date_from"])).days + 1
        for g in segments
    )
    chart = next(iter(fares.family(family).charts.values()))
    out: dict[str, Any] = {
        "status": "ok" if segments else "not_found",
        **base,
        "season_group": group,
        "segments": segments,
        "chart": {
            "source_url": chart.source_url,
            "fetched_at": chart.fetched_at.astimezone(JST).isoformat(),
        },
    }
    if covered < (date_to - date_from).days + 1:
        out["caveat"] = "期間の一部は収録カレンダー外のためシーズンを判定できません"
    if note:
        out["note"] = note
    return out


def build_tools(
    fares: FareStore, index: RulesIndex | None, evidence: Evidence, lang: str
) -> list[StructuredTool]:
    """The model's tools; every result it may cite is added to `evidence`. A rule note is
    kept in the answer language `lang` only, the one the system shows the user."""

    def required_miles(**kwargs: Any) -> str:
        evidence.add_itinerary(kwargs.get("itinerary"))
        res = get_required_miles(fares, **kwargs).to_dict()
        # "Not offered" (設定なし) and other definite answers are facts from the chart too,
        # and models cite them; only a clarification request carries no evidence.
        if res["status"] != "needs_clarification":
            res = {"evidence_id": evidence.add_chart(res), **res}
        return json.dumps(res, ensure_ascii=False, default=str)

    def season(airline: str, destination: str, date_from: date, date_to: date | None = None) -> str:
        res = season_periods(fares, airline, destination, date_from, date_to)
        if res["status"] == "ok":
            res = {"evidence_id": evidence.add_chart(res), **res}
        return json.dumps(res, ensure_ascii=False, default=str)

    def rules(
        question: str,
        airline: str | None = None,
        award_type: str | None = None,
        itinerary: list[list[str]] | None = None,
    ) -> str:
        evidence.add_itinerary(itinerary)
        res = search_rules(
            index or get_rules_index(),
            question,
            airline=airline,
            award_type=award_type,
            itinerary_flags=frozenset(evidence.itinerary_flags),
        )
        for key in ("input_tokens", "output_tokens"):
            evidence.tool_usage[key] += res.usage.get(key, 0)
        out: dict[str, Any] = {"status": res.status, "chunks": []}
        for chunk in res.chunks:
            if chunk.get("editor_note"):
                chunk = {**chunk, "editor_note": chunk["editor_note"][lang]}
            out["chunks"].append({"evidence_id": evidence.add_doc(chunk), **chunk})
        return json.dumps(out, ensure_ascii=False)

    return [
        StructuredTool.from_function(
            func=rules,
            name="search_rules",
            description=(
                "Search the airlines' official award-ticket rule pages (conditions, changes, "
                "cancellation and refund fees, booking) and return relevant excerpts with "
                "URLs and evidence ids. Pass the user's question; Japanese queries work best."
            ),
            args_schema=RulesArgs,
        ),
        StructuredTool.from_function(
            func=required_miles,
            name="get_required_miles",
            description=(
                "Required award miles from the airline's published award chart, computed by code. "
                "Returns all seasons (L/R/H), the season and total for given dates, the chart "
                "version and source URL, and an evidence_id to cite."
            ),
            args_schema=MilesArgs,
        ),
        StructuredTool.from_function(
            func=season,
            name="get_season",
            description=(
                "Award seasons (L/R/H) for a travel date or a whole period on a trip between "
                "Japan and a destination: returns each date range with its season, the source "
                "URL and an evidence_id to cite. Use it whenever the user gives months or dates."
            ),
            args_schema=SeasonArgs,
        ),
    ]
