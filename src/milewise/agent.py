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
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
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
show the L/R/H values. If neither the question nor \
the earlier turns (rule 9) say round trip or one way, answer for one way (片道) and say so in \
one short line. Pass travel dates when the user \
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
partner awards are distance-based (the sum of every flown segment): pass total_distance_miles \
only if the user gives it. When the user names connections, stopovers or an open jaw, pass \
the whole trip as itinerary, in the user's order: one list per stretch flown without a break, \
e.g. 东京-香港-伦敦 round trip = [["东京","香港","伦敦","香港","东京"]]; to London, back \
from Paris = [["東京","ロンドン"],["パリ","東京"]] (London-Paris by land is not counted). \
A place the tool does not know by name can be given by its IATA airport code (e.g. "LIM"); \
the route then shows the airport's name, so check it is the place the user meant. \
Without it the tool assumes a nonstop route. Show the route the tool returns ("route") so \
the user can check it, present the distance ("calculation") as an estimate with its basis, \
show every band returned (two when the estimate is near a band edge), and say the exact \
figure depends on JAL's segment mileage. Never estimate or add up a distance yourself.
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
7. Do not add a "Sources" / 出典 / 来源 section or list sources at the end: the page shows \
the source of every evidence id you cite. Citing the ids in the text (rules 2 and 5) is enough.
8a. Call every tool you need first, then write the whole answer once, after the last tool \
result: no text before or between tool calls.
9. Earlier turns of the conversation may precede the last question. Use them only to \
understand it: a follow-up such as "那经济舱呢" or "片道なら？" keeps the route, airline, cabin, \
trip type and dates of the previous question except what it changes. A follow-up that \
only names a new route keeps the trip type: after a round-trip answer, "东京-香港-伦敦 这样呢" \
asks for a round trip Tokyo-Hong Kong-London (trip_type RT). Answer only the last \
question. Figures and evidence ids from earlier answers are not evidence for this one: call \
the tools again in this turn and cite only what they return now. Earlier answers are shown \
without their evidence ids; this answer must still put an id from this turn after every \
figure and rule statement, as in rules 2 and 5. Rule excerpts shown under an earlier answer \
are what that answer was based on: use them to follow the conversation, then call \
search_rules again to cite them.
10. When the user disputes an answer or offers their own reading of a rule, call search_rules \
again and quote the sentence of the excerpt that decides the point. Change your answer only if \
that text supports the change; if it does not, say so and keep your answer, explaining why. \
Never just agree. Itineraries the user reports as actually ticketed (e.g. in a post they \
shared) are real examples: check your conclusion against them and point out any conflict.
11. State only rules that are in this turn's excerpts; do not fill gaps from general knowledge. \
When the excerpts do not settle a point (a term they do not define, such as what 日本発着 \
covers or whether two places count as the same country), say that the official text does not \
define it, give the possible readings and what each would mean for the user's trip, and \
suggest confirming with the airline. Never present one reading as the rule.
"""
RETRY_NOTE = """
Your previous attempt at this question answered without calling any tool. Earlier turns and \
your own earlier answers are not evidence. Call the tools that cover this question now \
(search_rules for rules, conditions and itinerary limits; get_required_miles for miles) and \
answer only from what they return. Answer without tools only if the question is outside \
Milewise's scope (rule 4b) or is not about award tickets at all.
"""

OFFICIAL_URLS = {
    "ANA international award chart (zones, seasons, required miles)": "https://www.ana.co.jp/ja/jp/guide/amc/award/international/terms/",
    "JAL international award required miles": "https://www.jal.co.jp/jp/ja/jalmile/use/jal/inter/routemiles.html",
}
LANGUAGES = {"ja": "Japanese", "zh": "Simplified Chinese", "en": "English"}
# Appended when even the retry (RETRY_NOTE) answered without looking anything up.
UNCHECKED_NOTE = {
    "ja": "\n\nℹ️ この回答は公式資料を参照していません。",
    "zh": "\n\nℹ️ 本回答没有查阅官方资料。",
    "en": "\n\nℹ️ This answer was not checked against the official sources.",
}
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

    def rules(question: str, airline: str | None = None, award_type: str | None = None) -> str:
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
# Rule excerpts an earlier answer cited, shown to the model under that answer (newest first),
# up to this many characters in all. A count limit (6 chunks) used to cut off half of a page
# once search_rules returned whole pages (design.md §4.13).
HISTORY_EXCERPT_CHARS = 6000
_CITE_MARK = re.compile(r"\s*\[[CD]\d+\]")
EXCERPTS_HEADER = (
    "[Rule excerpts this answer was based on. Context only: call search_rules again to cite them.]"
)


def prior_turns(
    history: list[dict[str, Any]] | None,
    excerpt: Callable[[str], dict[str, Any] | None] | None = None,
) -> list[dict[str, str]]:
    """The first and the last HISTORY_TURNS user/assistant pairs, trimmed, as chat messages.

    The first question stays because it often sets the topic (a shared post, an itinerary)
    that later follow-ups test conclusions against. Earlier evidence ids ([C1]) and the
    post-check warning are removed from answers, so the model cannot cite evidence that does
    not exist in this turn. With `excerpt` (chunk id -> chunk), the rule excerpts an answer
    cited (its "evidence" chunk ids) are appended under it, so a follow-up sees the official
    text rather than only the model's own paraphrase of it.
    """
    out: list[dict[str, Any]] = []
    for m in history or []:
        role, text = m.get("role"), str(m.get("content") or "")
        if role not in ("user", "assistant") or not text.strip():
            continue
        if role == "assistant":
            for w in [*POSTCHECK_WARNING.values(), *UNCHECKED_NOTE.values()]:
                text = text.replace(w, "")
            text = _CITE_MARK.sub("", text)
        ids = [str(i) for i in (m.get("evidence") or [])] if role == "assistant" else []
        out.append({"role": role, "content": text.strip()[:HISTORY_CHARS], "evidence": ids})
    # the conversation must alternate and start with the user
    while out and out[0]["role"] != "user":
        out.pop(0)
    merged: list[dict[str, Any]] = []
    for m in out:
        if merged and merged[-1]["role"] == m["role"]:
            merged[-1] = m
        else:
            merged.append(m)
    if merged and merged[-1]["role"] == "user":
        merged.pop()  # an unanswered question is not context
    pairs = [merged[i : i + 2] for i in range(0, len(merged), 2)]
    if len(pairs) > HISTORY_TURNS:
        pairs = [pairs[0], *pairs[-HISTORY_TURNS:]]
    kept = [m for pair in pairs for m in pair]

    # newest answers first, so a chunk cited again is shown under its latest answer
    shown: set[str] = set()
    room = HISTORY_EXCERPT_CHARS
    for m in reversed(kept):
        chunks = []
        for cid in m["evidence"] if excerpt else []:
            c = excerpt(cid) if excerpt and cid not in shown else None
            if c and len(c["text"]) <= room:
                shown.add(cid)
                room -= len(c["text"])
                chunks.append(c["text"])
        if chunks:
            m["content"] += "\n\n" + EXCERPTS_HEADER + "\n" + "\n---\n".join(chunks)
    return [{"role": m["role"], "content": m["content"]} for m in kept]


def _stream(
    agent: Any,
    inputs: dict[str, Any],
    on_event: Callable[[str, dict[str, Any]], None],
    hold: bool = False,
) -> dict[str, Any]:
    """Run the agent, forwarding tool starts and answer text. With `hold`, text is kept back
    until the first tool call: an answer written without any lookup is retried, and the page
    should not show it first."""
    state: dict[str, Any] = {}
    held: list[str] | None = [] if hold else None
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
                if held:
                    on_event("delta", {"text": "".join(held)})
                held = None
                on_event("tool", {"name": tc["name"]})
        if chunk.text:
            if held is None:
                on_event("delta", {"text": chunk.text})
            else:
                held.append(chunk.text)
    return state


def answer_text(messages: list[Any]) -> str:
    """Everything the model wrote after the last user message, in order.

    Not only the last message: text written before a further tool call is part of the
    answer too (seen with GLM-5: the mileage, then a rules lookup, then a note).
    """
    start = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1)
    parts = [str(m.text).strip() for m in messages[start + 1 :] if isinstance(m, AIMessage)]
    return "\n\n".join(p for p in parts if p)


def _excerpt_lookup(index: RulesIndex | None) -> Callable[[str], dict[str, Any] | None]:
    """chunk id -> indexed chunk; ids the index does not have (or a client made up) are None."""
    idx = index or get_rules_index()
    return idx.chunk


def ask(
    question: str,
    fares: FareStore | None = None,
    index: RulesIndex | None = None,
    settings: Settings | None = None,
    model: str | None = None,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
    history: list[dict[str, Any]] | None = None,
) -> AskResult:
    """Answer one question. With `on_event`, the run is streamed: ("tool", {"name"}) when the
    model starts a tool call and ("delta", {"text"}) for answer text as it is generated.
    Text written before a later tool call is part of the answer (GLM-5 writes the main
    answer, then looks up a rule and adds to it), so a "tool" event only starts a new
    paragraph. The returned answer is authoritative (it may carry the post-check warning).

    `history` is the earlier turns of the conversation, oldest first, as {"role": "user" |
    "assistant", "content": text, "evidence": [chunk ids the answer cited]}; they only give
    context (system prompt rule 9). An attempt that calls no tool at all is retried once
    with RETRY_NOTE; its text is not streamed.
    """
    s = settings or get_settings()
    agent_model = model or s.chat_model
    lang = detect_language(question)
    evidence = Evidence()
    tools = build_tools(fares or get_fare_store(), index, evidence)
    system_prompt = SYSTEM_PROMPT.format(
        today=today_jst().isoformat(),
        language=LANGUAGES[lang],
        official_urls="\n".join(f"- {k}: {v}" for k, v in OFFICIAL_URLS.items()),
    )

    def run(prompt: str, hold: bool) -> list[Any]:
        # Hard caps so a confused model cannot loop and run up cost (design.md R7).
        middleware: list[Any] = [
            ModelCallLimitMiddleware(run_limit=5, exit_behavior="end"),
            # 8: a period question across cabins needs a season lookup plus one call per cabin.
            ToolCallLimitMiddleware(run_limit=8, exit_behavior="continue"),
        ]
        agent = create_agent(
            llm.chat_model(agent_model, s),
            tools=tools,
            system_prompt=prompt,
            middleware=middleware,
        )
        if on_event is None:
            return list(agent.invoke(inputs)["messages"])
        return list(_stream(agent, inputs, on_event, hold=hold)["messages"])

    t0 = time.perf_counter()
    excerpt = _excerpt_lookup(index) if any(m.get("evidence") for m in history or []) else None
    inputs: Any = {
        "messages": [*prior_turns(history, excerpt), {"role": "user", "content": question}]
    }
    messages = run(system_prompt, hold=True)
    spent = messages
    if not evidence.items:
        # Answered from memory (seen in follow-ups: invented rules, 2026-10-22). Retry once,
        # told to look things up; the first attempt's text was held back from the page.
        messages = run(system_prompt + RETRY_NOTE, hold=False)
        spent = spent + messages
    latency_ms = round((time.perf_counter() - t0) * 1000)

    answer = answer_text(messages)
    check = postcheck(answer, evidence)
    if not check.ok:
        answer += POSTCHECK_WARNING[lang]
    elif not evidence.items:
        answer += UNCHECKED_NOTE[lang]
    tool_calls = [
        {"name": tc["name"], "args": tc["args"]}
        for m in messages
        if isinstance(m, AIMessage)
        for tc in m.tool_calls
    ]
    usage = _usage(spent)
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
