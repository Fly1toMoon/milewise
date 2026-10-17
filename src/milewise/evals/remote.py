"""Ask a deployed endpoint (e.g. the Lambda `candidate` alias) instead of the in-process agent.

Used by CI to evaluate exactly the code that would be promoted (design.md M5).
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from milewise.agent import AskResult, PostCheck


def ask_remote(
    url: str, token: str, question: str, timeout: float = 180, client: httpx.Client | None = None
) -> AskResult:
    events: dict[str, dict[str, Any]] = {}
    t0 = time.perf_counter()
    buf = ""
    with (client or httpx.Client()).stream(
        "POST",
        f"{url.rstrip('/')}/ask",
        json={"question": question},
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout,
    ) as resp:
        resp.raise_for_status()
        for chunk in resp.iter_text():
            buf += chunk
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                lines = dict(
                    ln.split(": ", 1)
                    for ln in block.splitlines()
                    if ": " in ln and not ln.startswith(":")
                )
                if "event" in lines:
                    events[lines["event"]] = json.loads(lines["data"])
    if "error" in events or "answer" not in events or "done" not in events:
        raise RuntimeError(f"incomplete response from {url}: {sorted(events)}")
    done = events["done"]
    usage = {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0, "model_calls": 0}
    usage |= done.get("usage", {})
    return AskResult(
        question=question,
        answer=events["answer"]["text"],
        evidence={eid: {"url": u} for eid, u in (done.get("evidence") or {}).items() if u},
        postcheck=PostCheck(bool(done["postcheck_ok"]), [], [], []),
        tool_calls=[{"name": n, "args": {}} for n in done.get("tools", [])],
        usage=usage,
        cost_usd=float(done["cost_usd"]),
        latency_ms=round((time.perf_counter() - t0) * 1000),  # end to end, incl. network
        model=str(done["model"]),
    )
