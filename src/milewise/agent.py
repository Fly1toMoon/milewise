"""The question-answering agent (design.md §1.2, §3).

M1 scope: required-miles questions via structured tools. Rule questions get an honest
"not covered yet" until search_rules lands in M2.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from milewise.config import Settings, cost_usd, get_settings
from milewise.db import Db
from milewise.locations import resolve_city
from milewise.rag.search import search_rules
from milewise.tools.miles import (
    JAPAN_ZONE,
    SUPPORTED_FAMILIES,
    get_required_miles,
    season_for,
    today_jst,
)

SYSTEM_PROMPT = """\
You are Milewise, an assistant for award tickets (特典航空券) on ANA Mileage Club and \
JAL Mileage Bank. Today in Japan is {today}.

Rules:
1. Write the whole answer in {language}, the language of the user's question, even though \
tool results are in Japanese.
2. Never state a mileage figure from memory. Every required-miles number must come from \
get_required_miles in this conversation, written with digits and thousands separators \
(e.g. 110,000), immediately followed by the evidence id it came from, e.g. "110,000マイル [C1]". \
Do not write 万 or round numbers.
3. If the user does not name an airline, call get_required_miles for both NH (ANA) and JL (JAL) \
and report each, including when one of them is not covered. If the user gives no travel dates, \
show the L/R/H values. If they do not say round trip \
or one way, assume round trip and say so. Pass travel dates when the user gives them.
4. If a tool returns needs_clarification, give what is known and ask one short question. \
If it returns not_found or unsupported, say clearly that you cannot confirm it, and do not guess.
5. For rules, conditions, changes, cancellation/refund fees and similar questions, call \
search_rules and answer only from the excerpts it returns. Put the evidence id after each \
statement, e.g. "マイルは口座に戻ります [D1]", and quote only short phrases. If it returns \
no_evidence, or the excerpts do not answer the question, say clearly that you are not sure.
8. Only include URLs that appear in tool results or in this list of verified official pages:
{official_urls}
6a. When a result has price_kind "floor" (JAL 基本マイル数), present those figures as the \
minimum, never as the exact price: say 特典航空券PLUS can raise it depending on seats, up to \
the "ceiling" figures returned, and that the exact miles for a date come from JAL's seat search.
6b. When results contain several variants (different route labels, e.g. by departure airport \
or flight number), show each variant with its label, or ask which airport/flight is meant; \
never pick one silently.
6. Mention that fuel surcharges and taxes are extra, and that award seats are not guaranteed.
7. End with a short "Sources" section: for each chart its chart_id, the booking date it \
applies from, its source URL and fetch date; for each document excerpt its title, URL and \
fetch date.
"""

OFFICIAL_URLS = {
    "ANA international award chart (zones, seasons, required miles)": "https://www.ana.co.jp/ja/jp/guide/amc/award/international/terms/",
    "JAL international award required miles": "https://www.jal.co.jp/jp/ja/jalmile/use/jal/inter/routemiles.html",
}
LANGUAGES = {"ja": "Japanese", "zh": "Simplified Chinese", "en": "English"}
POSTCHECK_WARNING = {
    "ja": "\n\n⚠️ 自動チェック: 根拠データにない数値・引用・リンクが含まれています。公式サイトで必ずご確認ください。",
    "zh": "\n\n⚠️ 自动检查：回答中含有依据数据之外的数字、引用或链接，请务必以官网为准。",
    "en": "\n\n⚠️ Automatic check: this answer contains numbers, citations or links not backed by the source data. Please verify on the official site.",
}

# ASCII URL characters only, so trailing CJK punctuation ("…/refund/（取得日", "/，") is not
# swallowed into the URL. Shared with the eval grader.
URL_RE = re.compile(r"https?://[A-Za-z0-9\-._~:/?#@!$&*+=%]+")
_KANA = re.compile(r"[\u3040-\u30ff]")
_CJK = re.compile(r"[\u4e00-\u9fff]")


def detect_language(text: str) -> str:
    """Cheap heuristic until the M2 router: kana means Japanese, other CJK means Chinese."""
    if _KANA.search(text):
        return "ja"
    if _CJK.search(text):
        return "zh"
    return "en"


_MILES_NUMBER = re.compile(
    r"(\d{1,3}(?:[,，]\d{3})+|\d{4,})\s*(?:マイル|miles?|英里|里程|哩)", re.IGNORECASE
)
_EVIDENCE_MARK = re.compile(r"\[([CD]\d+)\]")


@dataclass
class Evidence:
    items: dict[str, dict[str, Any]] = field(default_factory=dict)
    allowed_miles: set[int] = field(default_factory=set)
    allowed_urls: set[str] = field(default_factory=lambda: set(OFFICIAL_URLS.values()))
    # Tokens spent inside tools (the reranker), so per-request cost is complete.
    tool_usage: dict[str, int] = field(
        default_factory=lambda: {"input_tokens": 0, "output_tokens": 0}
    )

    def add_chart(self, result: dict[str, Any]) -> str:
        eid = f"C{sum(k.startswith('C') for k in self.items) + 1}"
        self.items[eid] = result
        for r in result.get("results", []):
            self.allowed_miles.update({r["miles"], r["miles"] // 2})
        if result.get("total_miles"):
            self.allowed_miles.add(result["total_miles"])
        self.allowed_miles.update(result.get("totals", {}).values())
        if result.get("chart", {}).get("source_url"):
            self.allowed_urls.add(result["chart"]["source_url"])
        return eid

    def add_doc(self, chunk: dict[str, Any]) -> str:
        eid = f"D{sum(k.startswith('D') for k in self.items) + 1}"
        self.items[eid] = chunk
        self.allowed_urls.add(chunk["url"])
        # Figures quoted from a rule (e.g. a 3,000-mile fee) are backed by the document.
        self.allowed_miles.update(
            int(re.sub(r"[,，]", "", n)) for n in _ANY_NUMBER.findall(chunk["text"])
        )
        return eid


_ANY_NUMBER = re.compile(r"\d{1,3}(?:[,，]\d{3})+|\d+")


@dataclass
class PostCheck:
    ok: bool
    unsupported_numbers: list[int]
    unknown_marks: list[str]
    unknown_urls: list[str]


def postcheck(answer: str, evidence: Evidence) -> PostCheck:
    """Deterministic checks: no number, citation or link that the tools did not produce."""
    numbers = [int(re.sub(r"[,，]", "", m)) for m in _MILES_NUMBER.findall(answer)]
    bad = [n for n in numbers if n not in evidence.allowed_miles]
    marks = [m for m in _EVIDENCE_MARK.findall(answer) if m not in evidence.items]
    urls = [u.rstrip(".:") for u in URL_RE.findall(answer)]
    bad_urls = [u for u in urls if u not in evidence.allowed_urls]
    return PostCheck(
        ok=not bad and not marks and not bad_urls,
        unsupported_numbers=bad,
        unknown_marks=marks,
        unknown_urls=bad_urls,
    )


class MilesArgs(BaseModel):
    airline: Literal["NH", "JL"] = Field(description="NH = ANA, JL = JAL")
    origin: str = Field(description="Departure city/airport/IATA code, any language")
    destination: str = Field(description="Arrival city/airport/IATA code, any language")
    cabin: Literal["Y", "PY", "C", "F"] = Field(
        description="Y economy, PY premium economy, C business, F first"
    )
    trip_type: Literal["RT", "OW"] = Field(default="RT", description="RT round trip, OW one way")
    outbound_date: date | None = Field(default=None, description="Outbound travel date, if known")
    return_date: date | None = Field(default=None, description="Return travel date (RT), if known")
    booking_date: date | None = Field(
        default=None, description="Booking/ticketing date; default today"
    )
    flight_number: str | None = Field(
        default=None, description="Flight number if the user gives one (e.g. JL031)"
    )
    award_type: Literal["own", "partner"] = Field(
        default="own", description="own-metal or partner award"
    )


class RulesArgs(BaseModel):
    question: str = Field(description="What to look up, ideally rewritten in Japanese")
    airline: Literal["NH", "JL"] | None = Field(default=None, description="Limit to one airline")


class SeasonArgs(BaseModel):
    airline: Literal["NH", "JL"]
    destination: str = Field(description="Non-Japan end of the trip (city/airport/IATA code)")
    travel_date: date


def build_tools(db: Db, evidence: Evidence) -> list[StructuredTool]:
    def required_miles(**kwargs: Any) -> str:
        res = get_required_miles(db, **kwargs).to_dict()
        if res["status"] == "ok":
            res = {"evidence_id": evidence.add_chart(res), **res}
        return json.dumps(res, ensure_ascii=False, default=str)

    def season(airline: str, destination: str, travel_date: date) -> str:
        family = SUPPORTED_FAMILIES.get((airline, "own"))
        cities = resolve_city(destination)
        if family is None or len(cities) != 1:
            return json.dumps(
                {"status": "not_found", "destination": destination}, ensure_ascii=False
            )
        zones = db.query(
            "SELECT zone_code FROM fare.zone_members WHERE chart_family = :f AND"
            " ((match_kind = 'city' AND match_value = :c) OR (match_kind = 'country' AND match_value = :n))",
            {"f": family, "c": cities[0].city_code, "n": cities[0].country},
        )
        s = (
            season_for(db, family, JAPAN_ZONE, zones[0]["zone_code"], travel_date)
            if zones
            else None
        )
        return json.dumps(
            {"status": "ok" if s else "not_found", "season": s, "travel_date": str(travel_date)},
            ensure_ascii=False,
        )

    def rules(question: str, airline: str | None = None) -> str:
        res = search_rules(db, question, airline=airline)
        for key in ("input_tokens", "output_tokens"):
            evidence.tool_usage[key] += res.usage.get(key, 0)
        out: dict[str, Any] = {"status": res.status, "chunks": []}
        for chunk in res.chunks:
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
            description="Award season (L/R/H) of a travel date for a trip between Japan and a destination.",
            args_schema=SeasonArgs,
        ),
    ]


@dataclass
class AskResult:
    question: str
    answer: str
    evidence: dict[str, dict[str, Any]]
    postcheck: PostCheck
    tool_calls: list[dict[str, Any]]
    usage: dict[str, int]
    cost_usd: float
    latency_ms: int
    model: str


def _usage(messages: list[Any]) -> dict[str, int]:
    total = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "model_calls": 0}
    for m in messages:
        if isinstance(m, AIMessage) and m.usage_metadata:
            u = m.usage_metadata
            total["model_calls"] += 1
            total["input_tokens"] += u.get("input_tokens", 0)
            total["output_tokens"] += u.get("output_tokens", 0)
            total["cached_input_tokens"] += (u.get("input_token_details") or {}).get(
                "cache_read", 0
            )
    return total


def ask(
    question: str, db: Db, settings: Settings | None = None, model: str | None = None
) -> AskResult:
    s = settings or get_settings()
    evidence = Evidence()
    llm = ChatOpenAI(
        model=model or s.openai_model,
        api_key=s.openai_api_key or None,  # type: ignore[arg-type]
        use_responses_api=True,
        reasoning={"effort": s.openai_reasoning_effort},
        max_tokens=4000,  # type: ignore[call-arg]
    )
    # Hard caps so a confused model cannot loop and run up cost (design.md R7).
    middleware: list[Any] = [
        ModelCallLimitMiddleware(run_limit=5, exit_behavior="end"),
        ToolCallLimitMiddleware(run_limit=4, exit_behavior="continue"),
    ]
    agent = create_agent(
        llm,
        tools=build_tools(db, evidence),
        system_prompt=SYSTEM_PROMPT.format(
            today=today_jst().isoformat(),
            language=LANGUAGES[detect_language(question)],
            official_urls="\n".join(f"- {k}: {v}" for k, v in OFFICIAL_URLS.items()),
        ),
        middleware=middleware,
    )
    t0 = time.perf_counter()
    state = agent.invoke({"messages": [{"role": "user", "content": question}]})
    latency_ms = round((time.perf_counter() - t0) * 1000)

    messages = state["messages"]
    final = next(m for m in reversed(messages) if isinstance(m, AIMessage))
    answer: str = str(final.text)
    check = postcheck(answer, evidence)
    if not check.ok:
        answer += POSTCHECK_WARNING[detect_language(question)]
    tool_calls = [
        {"name": tc["name"], "args": tc["args"]}
        for m in messages
        if isinstance(m, AIMessage)
        for tc in m.tool_calls
    ]
    agent_model = model or s.openai_model
    usage = _usage(messages)
    # The agent runs on agent_model; the reranker inside search_rules always uses
    # the default (small) model, so each is priced separately.
    cost = cost_usd(
        agent_model, usage["input_tokens"], usage["cached_input_tokens"], usage["output_tokens"]
    ) + cost_usd(
        s.openai_model, evidence.tool_usage["input_tokens"], 0, evidence.tool_usage["output_tokens"]
    )
    usage["input_tokens"] += evidence.tool_usage["input_tokens"]
    usage["output_tokens"] += evidence.tool_usage["output_tokens"]
    return AskResult(
        question=question,
        answer=answer,
        evidence=evidence.items,
        postcheck=check,
        tool_calls=tool_calls,
        usage=usage,
        cost_usd=round(cost, 6),
        latency_ms=latency_ms,
        model=model or s.openai_model,
    )
