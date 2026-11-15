"""The question-answering agent (design.md §1.2, §3, §4.13).

Facts come from code and data, the model only writes them up, and the system checks what it
wrote. ask() is three stages, and each behaviour belongs to one of them:

1. ground (ground.py): the tools, what this turn must look up first, and one retry of a
   draft that looked nothing up;
2. write (prompt.py): the model writes the answer from this turn's evidence;
3. verify (verify.py): code checks and, on rule follow-ups, a model check; an answer with
   issues is rewritten once, and what the code checks still find is flagged.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from milewise import llm
from milewise.charts.store import FareStore, get_fare_store
from milewise.config import Settings, cost_usd, get_settings
from milewise.evidence import Evidence, normalize_marks
from milewise.ground import ForceTool, build_tools, conversation_evidence, grounded_draft
from milewise.prompt import detect_language, system_prompt
from milewise.rag.index import RulesIndex, get_rules_index
from milewise.verify import (
    POSTCHECK_WARNING,
    UNCHECKED_NOTE,
    Issue,
    Verdict,
    code_issues,
    revise_note,
    verify,
)


@dataclass
class AskResult:
    question: str
    answer: str
    evidence: dict[str, dict[str, Any]]
    # the answer as returned passes the code checks (else it carries POSTCHECK_WARNING)
    postcheck_ok: bool
    tool_calls: list[dict[str, Any]]
    usage: dict[str, int]
    cost_usd: float
    latency_ms: int
    model: str
    # verification (design.md §4.13): issues found on the draft (each with its source, code
    # or model), whether it was rewritten, whether the model check ran, the code issues left
    # on the final answer, the check model's cost and the time verify and rewrite took.
    # None when the answer comes from a deployed endpoint.
    check: dict[str, Any] | None = None
    # the itinerary this turn's tools were given (else the latest one from history), for
    # the client to send back with this answer (history "itinerary")
    itinerary: list[list[str]] | None = None


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
    model starts a tool call, ("delta", {"text"}) for answer text, ("status", {"stage":
    "checking"}) when the model check starts and ("revise", {}) when the draft is dropped
    for a rewrite. The returned answer is authoritative.

    `history` is the earlier turns, oldest first, as {"role": "user" | "assistant",
    "content": text, "evidence": [chunk ids the answer cited], "itinerary": the trip that
    answer looked up}; it only gives context (system prompt rule 16).
    """
    s = settings or get_settings()
    lang = detect_language(question)
    evidence, carried = conversation_evidence(history)
    turn = _Turn(
        model or s.chat_model,
        s,
        system_prompt(lang),
        build_tools(fares or get_fare_store(), index, evidence),
        [*prior_turns(history, _excerpts(history, index)), {"role": "user", "content": question}],
        on_event,
    )

    # 1. Ground and 2. write: a draft that rests on this turn's lookups
    messages = grounded_draft(turn.run, history, evidence)

    # 3. Verify: every issue found goes into one rewrite; only code checks run after it
    t1 = time.perf_counter()
    verdict = verify(
        answer_text(messages),
        evidence,
        question=question,
        history=history,
        tools_called=[t["name"] for t in tool_calls(messages)],
        settings=s,
        on_event=on_event,
    )
    remaining: list[Issue] = []
    if verdict.issues:
        if on_event:
            on_event("revise", {})  # the page drops the streamed draft
        messages = turn.run(start=[*messages, HumanMessage(revise_note(verdict.issues))])
        remaining = code_issues(answer_text(messages), evidence)
    answer = answer_text(messages)
    if remaining:
        answer += POSTCHECK_WARNING[lang]
    elif not evidence.items:
        answer += UNCHECKED_NOTE[lang]
    return turn.result(
        question, answer, messages, evidence, verdict, remaining, t1, evidence.itinerary or carried
    )


class _Turn:
    """Agent runs of one question, with what they spend."""

    def __init__(
        self,
        model: str,
        settings: Settings,
        prompt: str,
        tools: list[Any],
        inputs: list[dict[str, str]],
        on_event: Callable[[str, dict[str, Any]], None] | None,
    ) -> None:
        self.model, self.settings, self.prompt, self.tools = model, settings, prompt, tools
        self.inputs, self.on_event = inputs, on_event
        self.spent: list[Any] = []
        self.t0 = time.perf_counter()

    def run(
        self,
        *,
        note: str = "",
        force: str | None = None,
        hold: bool = False,
        start: list[Any] | None = None,
    ) -> list[Any]:
        """A run on the question, or with `start`, a continuation of earlier messages."""
        # Hard caps so a confused model cannot loop and run up cost (design.md R7).
        middleware: list[Any] = [
            ModelCallLimitMiddleware(run_limit=5, exit_behavior="end"),
            # 8: a period question across cabins needs a season lookup plus one call per cabin.
            ToolCallLimitMiddleware(run_limit=8, exit_behavior="continue"),
        ]
        if force:
            middleware.append(ForceTool(force))
        agent = create_agent(
            llm.chat_model(self.model, self.settings),
            tools=self.tools,
            system_prompt=self.prompt + note,
            middleware=middleware,
        )
        payload: Any = {"messages": self.inputs if start is None else start}
        if self.on_event is None:
            messages = list(agent.invoke(payload)["messages"])
        else:
            messages = list(_stream(agent, payload, self.on_event, hold=hold)["messages"])
        self.spent += messages[len(start or []) :]
        return messages

    def result(
        self,
        question: str,
        answer: str,
        messages: list[Any],
        evidence: Evidence,
        verdict: Verdict,
        remaining: list[Issue],
        t_verify: float,
        itinerary: list[list[str]] | None,
    ) -> AskResult:
        now = time.perf_counter()
        usage = _usage(self.spent)
        # The agent runs on self.model; the reranker inside search_rules on rerank_model (also
        # when an A/B run overrides the agent model), so each is priced separately.
        cost = (
            cost_usd(
                self.model,
                usage["input_tokens"],
                usage["cached_input_tokens"],
                usage["output_tokens"],
            )
            + cost_usd(
                self.settings.rerank_model,
                evidence.tool_usage["input_tokens"],
                0,
                evidence.tool_usage["output_tokens"],
            )
            + verdict.cost_usd
        )
        usage["input_tokens"] += evidence.tool_usage["input_tokens"]
        usage["output_tokens"] += evidence.tool_usage["output_tokens"]
        return AskResult(
            question=question,
            answer=answer,
            evidence=evidence.items,
            postcheck_ok=not remaining,
            tool_calls=tool_calls(messages),
            usage=usage,
            cost_usd=round(cost, 6),
            latency_ms=round((now - self.t0) * 1000),
            model=self.model,
            check={
                "issues": [asdict(i) for i in verdict.issues],
                "revised": bool(verdict.issues),
                "model_checked": verdict.model_checked,
                "remaining": [asdict(i) for i in remaining],
                # what the rewrite cost is in `usage` already; this is the check model's call
                "cost_usd": round(verdict.cost_usd, 6),
                "latency_ms": round((now - t_verify) * 1000),
            },
            itinerary=itinerary,
        )


def answer_text(messages: list[Any]) -> str:
    """Everything the model wrote after the last user message, in order, with citation marks
    normalised.

    Not only the last message: text written before a further tool call is part of the
    answer too (seen with GLM-5: the mileage, then a rules lookup, then a note).
    """
    start = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1)
    parts = [str(m.text).strip() for m in messages[start + 1 :] if isinstance(m, AIMessage)]
    return normalize_marks("\n\n".join(p for p in parts if p))


def tool_calls(messages: list[Any]) -> list[dict[str, Any]]:
    return [
        {"name": tc["name"], "args": tc["args"]}
        for m in messages
        if isinstance(m, AIMessage)
        for tc in m.tool_calls
    ]


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


# ---------- earlier turns ----------

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


def _excerpts(
    history: list[dict[str, Any]] | None, index: RulesIndex | None
) -> Callable[[str], dict[str, Any] | None] | None:
    """chunk id -> indexed chunk (None for ids the index does not have, or a client made
    up); None when no earlier answer cited any."""
    if not any(m.get("evidence") for m in history or []):
        return None
    return (index or get_rules_index()).chunk


def prior_turns(
    history: list[dict[str, Any]] | None,
    excerpt: Callable[[str], dict[str, Any] | None] | None = None,
) -> list[dict[str, str]]:
    """The first and the last HISTORY_TURNS user/assistant pairs, trimmed, as chat messages.

    The first question stays because it often sets the topic (a shared post, an itinerary)
    that later follow-ups test conclusions against. What the system added to an answer (the
    post-check warning, the unchecked note) and earlier evidence ids ([C1]) are removed, so
    the model neither takes them for its own words nor cites evidence that does not exist in
    this turn. With `excerpt` (chunk id -> chunk), the rule excerpts an answer cited (its
    "evidence" chunk ids) are appended under it, so a follow-up sees the official text rather
    than only the model's own paraphrase of it.
    """
    out: list[dict[str, Any]] = []
    for m in history or []:
        role, text = m.get("role"), str(m.get("content") or "")
        if role not in ("user", "assistant") or not text.strip():
            continue
        if role == "assistant":
            for w in [*POSTCHECK_WARNING.values(), *UNCHECKED_NOTE.values()]:
                text = text.replace(w, "")
            text = _CITE_MARK.sub("", normalize_marks(text))
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
