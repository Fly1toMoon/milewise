"""HTTP entry point: the same app runs in Lambda (via Lambda Web Adapter) and locally.

    uv run --env-file .env uvicorn milewise.api:app --port 8080

Routes
  GET  /         single-page UI
  GET  /healthz  readiness check for the adapter
  POST /ask      Bearer token, daily quota, Server-Sent Events: status → answer → done

Charts and the rules index ship in the image (design.md §4.6) and are loaded at init.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
import uuid
from collections.abc import AsyncIterator
from functools import cache
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from milewise.charts.store import get_fare_store
from milewise.config import get_settings
from milewise.obs import emit
from milewise.quota import UsageCounter, counter
from milewise.rag.index import get_rules_index
from milewise.tools.miles import today_jst

STATIC = Path(__file__).parent / "static"


def load_secrets() -> bool:
    """Resolve Secrets Manager ARNs into env vars; True when everything is available.

    Never raises: a secret without a value (e.g. the OpenAI key before `make
    put-openai-key`) must not crash the function at init, which would also take down
    /healthz. Callers retry on the next request.
    """
    pairs = {"OPENAI_API_KEY": "OPENAI_API_KEY_SECRET_ARN", "APP_TOKEN": "APP_TOKEN_SECRET_ARN"}
    todo = {
        env: os.environ[arn]
        for env, arn in pairs.items()
        if os.environ.get(arn) and not os.environ.get(env)
    }
    if not todo:
        return True
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    sm = boto3.client("secretsmanager")
    ok = True
    for env, arn in todo.items():
        try:
            os.environ[env] = sm.get_secret_value(SecretId=arn)["SecretString"].strip()
        except (ClientError, BotoCoreError) as e:
            ok = False
            emit("secret_unavailable", env=env, error_type=type(e).__name__)
    get_settings.cache_clear()
    return ok


def require_secrets() -> None:
    if not load_secrets() or not get_settings().openai_api_key:
        raise HTTPException(503, "service not configured yet")


load_secrets()
app = FastAPI(title="Milewise", docs_url=None, redoc_url=None)


@cache
def usage() -> UsageCounter:
    return counter(get_settings().usage_table)


def data_version() -> dict[str, Any]:
    """Which rules index this process serves (None when it is missing, e.g. local dev)."""
    try:
        return {"rules_index": get_rules_index().version}
    except FileNotFoundError:
        return {"rules_index": None}


# Parse the bundled data during init (free in Lambda) rather than on the first question.
get_fare_store()
data_version()


class AskBody(BaseModel):
    question: str = Field(min_length=2, max_length=500)


def _sse(event: str, data: Any) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n".encode()


def _check_token(authorization: str | None) -> None:
    expected = get_settings().app_token
    given = (authorization or "").removeprefix("Bearer ").strip()
    if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(401, "missing or invalid token")


def _reserve_quota() -> None:
    s = get_settings()
    u = usage().add(today_jst().isoformat(), requests=1)
    if u.requests > s.daily_request_limit or u.cost_usd > s.daily_cost_limit_usd:
        raise HTTPException(429, "daily limit reached; try again tomorrow")


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"ok": True, **data_version()}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.post("/ask")
async def ask_endpoint(
    body: AskBody, authorization: str | None = Header(default=None)
) -> StreamingResponse:
    await run_in_threadpool(require_secrets)
    _check_token(authorization)
    await run_in_threadpool(_reserve_quota)
    request_id = str(uuid.uuid4())

    async def stream() -> AsyncIterator[bytes]:
        from milewise.agent import ask

        yield _sse("status", {"request_id": request_id, "stage": "working"})
        t0 = time.perf_counter()
        task = asyncio.ensure_future(run_in_threadpool(ask, body.question))
        while not task.done():  # keep the stream (and the user) alive while the agent works
            await asyncio.sleep(3)
            if not task.done():
                yield b": ping\n\n"
        try:
            res = task.result()
        except Exception as e:
            emit(
                "ask",
                request_id=request_id,
                error=1,
                error_type=type(e).__name__,
                latency_ms=round((time.perf_counter() - t0) * 1000),
            )
            yield _sse("error", {"request_id": request_id, "message": "internal error"})
            return
        await run_in_threadpool(usage().add, today_jst().isoformat(), 0, res.cost_usd)
        sources = sorted(
            {v.get("url") or v.get("chart", {}).get("source_url") for v in res.evidence.values()}
            - {None}
        )
        yield _sse("answer", {"request_id": request_id, "text": res.answer})
        yield _sse(
            "done",
            {
                "request_id": request_id,
                "sources": sources,
                # evidence id -> source URL, so a remote evaluator can check citations
                "evidence": {
                    eid: v.get("url") or v.get("chart", {}).get("source_url")
                    for eid, v in res.evidence.items()
                },
                "tools": [t["name"] for t in res.tool_calls],
                "usage": res.usage,
                "postcheck_ok": res.postcheck.ok,
                "latency_ms": res.latency_ms,
                "cost_usd": res.cost_usd,
                "model": res.model,
                "data": data_version(),
            },
        )
        emit(
            "ask",
            request_id=request_id,
            model=res.model,
            latency_ms=res.latency_ms,
            cost_usd=res.cost_usd,
            input_tokens=res.usage["input_tokens"],
            output_tokens=res.usage["output_tokens"],
            postcheck_failed=int(not res.postcheck.ok),
            tools=[t["name"] for t in res.tool_calls],
            question_sha256=hashlib.sha256(body.question.encode()).hexdigest()[:16],
        )

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Request-Id": request_id},
    )
