"""The question-answering agent (design.md §1.2, §3).

M1 scope: required-miles questions via structured tools. Rule questions get an honest
"not covered yet" until search_rules lands in M2.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from milewise import llm
from milewise.charts.store import FareStore, get_fare_store
from milewise.config import Settings, cost_usd, get_settings
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

SYSTEM_PROMPT = """\
You are Milewise, an assistant for award tickets (特典航空券) on ANA Mileage Club and \
JAL Mileage Bank. Today in Japan is {today}.

Rules:
1. Write the whole answer in {language}, the language of the user's question, even though \
tool results are in Japanese.
2. Never state a mileage figure from memory. Every required-miles number must come from \
get_required_miles in this conversation, written with digits and thousands separators \
(e.g. 110,000), immediately followed by the unit and the evidence id it came from, e.g. \
"110,000マイル [C1]". The unit is exactly マイル in Japanese, 里程 in Chinese, miles in English. \
Do not write 万 or round numbers.
3. If the user does not name an airline, call get_required_miles for both NH (ANA) and JL (JAL) \
and report each, including when one of them is not covered. If the user gives no travel dates, \
show the L/R/H values. If they do not say round trip \
or one way, answer for one way (片道) and say so in one short line. Pass travel dates when the user \
gives them.
3a. If the user names travel months or a period, call get_season once for the whole period \
(a month or month without a year means its next occurrence on or after today). Then group the \
answer by the returned date ranges: for each range give its dates, its season and the miles of \
that season, citing both evidence ids. Call get_required_miles once per cabin WITHOUT travel \
dates (it returns the figure of every season), in parallel; never once per date. If no cabin is \
named you must cover all three: economy (Y), premium economy (PY) and business (C).
4. If a tool returns needs_clarification, give what is known and ask one short question. \
If it returns not_found or unsupported, say clearly that you cannot confirm it, and do not guess. \
Questions about awards on partner airlines (提携航空会社 / 伙伴航空 / Star Alliance / oneworld) \
must be looked up with award_type "partner" (never as an own-metal award). Country or region \
names (中国, 欧洲) are fine as origin/destination for partner awards. ANA partner results \
from Japan come as Zone 1-A and Zone 1-B variants: show both with their conditions. JAL \
partner awards are distance-based: pass total_distance_miles only if the user gives it. \
Without it the tool estimates the distance from a nonstop great-circle route ("calculation"): \
present that figure as an estimate with its basis, show every band returned (two when the \
estimate is near a band edge), and say the exact figure depends on the actual flown distance \
and connections. Never estimate a distance yourself.
4b. Milewise covers only ANA Mileage Club and JAL Mileage Bank miles. For another airline's \
own program (e.g. Delta SkyMiles, United MileagePlus, Asia Miles) say it is not covered and \
give no figures; do not substitute ANA or JAL charts.
5. For rules, conditions, changes, cancellation/refund fees and similar questions, call \
search_rules and answer only from the excerpts it returns. Put the evidence id after each \
statement, e.g. "マイルは口座に戻ります [D1]", and quote only short phrases. If it returns \
no_evidence, or the excerpts do not answer the question, say clearly that you are not sure.
5a. Each excerpt has a "section" (its heading path), which says which award and which \
situation it covers. An excerpt under a special situation (e.g. 悪天候や機材故障などが理由の…, a revision \
notice) applies only to that situation: answer with the general rule first, then mention the \
special case as an exception. Never present a special-case rule as the general one.
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
9. Earlier turns of the conversation may precede the last question. Use them only to \
understand it: a follow-up such as "那经济舱呢" or "片道なら？" keeps the route, airline, cabin, \
trip type and dates of the previous question except what it changes. Answer only the last \
question. Figures and evidence ids from earlier answers are not evidence for this one: call \
the tools again in this turn and cite only what they return now.
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
            # distance-band labels ("総旅程距離 10,001–12,000 マイル") are citable too
            self.allowed_miles.update(
                int(re.sub(r"[,，]", "", n)) for n in _ANY_NUMBER.findall(r.get("variant", ""))
            )
        if result.get("total_miles"):
            self.allowed_miles.add(result["total_miles"])
        # figures inside the tool's own calculation text (e.g. an estimated distance)
        self.allowed_miles.update(
            int(re.sub(r"[,，]", "", n))
            for n in _ANY_NUMBER.findall(result.get("calculation") or "")
        )
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
    award_type: Literal["own", "partner"] = Field(
        default="own",
        description="own: flights of the airline itself. partner: an award on a partner "
        "airline (提携航空会社, 伙伴航空, Star Alliance, oneworld), also when the user only "
        "says a partner award without naming the airline",
    )


class RulesArgs(BaseModel):
    question: str = Field(description="What to look up, ideally rewritten in Japanese")
    airline: Literal["NH", "JL"] | None = Field(default=None, description="Limit to one airline")
    award_type: Literal["own", "partner"] = Field(
        default="own",
        description="own: awards on the airline's own flights (the default). partner: only "
        "when the user asks about partner-airline / oneworld / Star Alliance awards. Their "
        "rules differ (e.g. JAL partner awards can be changed after ticketing, JAL's own cannot)",
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
    fares: FareStore, index: RulesIndex | None, evidence: Evidence
) -> list[StructuredTool]:
    def required_miles(**kwargs: Any) -> str:
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

    def rules(question: str, airline: str | None = None, award_type: str = "own") -> str:
        res = search_rules(
            index or get_rules_index(), question, airline=airline, award_type=award_type
        )
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
            description=(
                "Award seasons (L/R/H) for a travel date or a whole period on a trip between "
                "Japan and a destination: returns each date range with its season, the source "
                "URL and an evidence_id to cite. Use it whenever the user gives months or dates."
            ),
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


# Bounds on what a client may send as context (each turn is also re-sent, so this caps cost).
HISTORY_TURNS = 3
HISTORY_CHARS = 4000
_CITE_MARK = re.compile(r"\s*\[[CD]\d+\]")


def prior_turns(history: list[dict[str, str]] | None) -> list[dict[str, str]]:
    """The last HISTORY_TURNS user/assistant pairs, trimmed, as chat messages.

    Earlier evidence ids ([C1]) and the post-check warning are removed from answers, so the
    model cannot cite evidence that does not exist in this turn.
    """
    out: list[dict[str, str]] = []
    for m in (history or [])[-2 * HISTORY_TURNS :]:
        role, text = m.get("role"), str(m.get("content") or "")
        if role not in ("user", "assistant") or not text.strip():
            continue
        if role == "assistant":
            for w in POSTCHECK_WARNING.values():
                text = text.replace(w, "")
            text = _CITE_MARK.sub("", text)
        out.append({"role": role, "content": text.strip()[:HISTORY_CHARS]})
    # the conversation must alternate and start with the user
    while out and out[0]["role"] != "user":
        out.pop(0)
    merged: list[dict[str, str]] = []
    for m in out:
        if merged and merged[-1]["role"] == m["role"]:
            merged[-1] = m
        else:
            merged.append(m)
    if merged and merged[-1]["role"] == "user":
        merged.pop()  # an unanswered question is not context
    return merged


def _stream(
    agent: Any, inputs: dict[str, Any], on_event: Callable[[str, dict[str, Any]], None]
) -> dict[str, Any]:
    state: dict[str, Any] = {}
    for mode, payload in agent.stream(inputs, stream_mode=["messages", "values"]):
        if mode == "values":
            state = payload
            continue
        chunk, meta = payload
        # only the agent's own model node; the reranker inside search_rules is not streamed
        if meta.get("langgraph_node") != "model" or not isinstance(chunk, AIMessageChunk):
            continue
        for tc in chunk.tool_call_chunks:
            if tc.get("name"):
                on_event("tool", {"name": tc["name"]})
        if chunk.text:
            on_event("delta", {"text": chunk.text})
    return state


def ask(
    question: str,
    fares: FareStore | None = None,
    index: RulesIndex | None = None,
    settings: Settings | None = None,
    model: str | None = None,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
    history: list[dict[str, str]] | None = None,
) -> AskResult:
    """Answer one question. With `on_event`, the run is streamed: ("tool", {"name"}) when the
    model starts a tool call and ("delta", {"text"}) for answer text as it is generated.
    Text that precedes a tool call is a preamble, not the answer, so a "tool" event tells
    the listener to discard what it has shown so far. The returned answer is authoritative
    (it may carry the post-check warning).

    `history` is the earlier turns of the conversation, oldest first, as {"role": "user" |
    "assistant", "content": text}; they only give context (system prompt rule 9).
    """
    s = settings or get_settings()
    agent_model = model or s.chat_model
    evidence = Evidence()
    # Hard caps so a confused model cannot loop and run up cost (design.md R7).
    middleware: list[Any] = [
        ModelCallLimitMiddleware(run_limit=5, exit_behavior="end"),
        # 8: a period question across cabins needs a season lookup plus one call per cabin.
        ToolCallLimitMiddleware(run_limit=8, exit_behavior="continue"),
    ]
    agent = create_agent(
        llm.chat_model(agent_model, s),
        tools=build_tools(fares or get_fare_store(), index, evidence),
        system_prompt=SYSTEM_PROMPT.format(
            today=today_jst().isoformat(),
            language=LANGUAGES[detect_language(question)],
            official_urls="\n".join(f"- {k}: {v}" for k, v in OFFICIAL_URLS.items()),
        ),
        middleware=middleware,
    )
    t0 = time.perf_counter()
    inputs: Any = {"messages": [*prior_turns(history), {"role": "user", "content": question}]}
    state = agent.invoke(inputs) if on_event is None else _stream(agent, inputs, on_event)
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
    usage = _usage(messages)
    # The agent runs on agent_model; the reranker inside search_rules runs on rerank_model
    # (also when an A/B run overrides the agent model), so each is priced separately.
    cost = cost_usd(
        agent_model, usage["input_tokens"], usage["cached_input_tokens"], usage["output_tokens"]
    ) + cost_usd(
        s.rerank_model, evidence.tool_usage["input_tokens"], 0, evidence.tool_usage["output_tokens"]
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
        model=agent_model,
    )
