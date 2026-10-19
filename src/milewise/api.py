"""HTTP entry point: the same app runs in Lambda (via Lambda Web Adapter) and locally.

    uv run --env-file .env uvicorn milewise.api:app --port 8080

Routes
  GET  /         single-page UI
  GET  /healthz  readiness check for the adapter
  GET  /config   public settings for the page (Turnstile site key)
  POST /ask      open to everyone behind milewise.guard (per-IP limits, answer cache,
                 site budget, kill switch); an owner Bearer token skips them.
                 Server-Sent Events: status → answer → done

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

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from milewise import llm
from milewise.charts.store import get_fare_store
from milewise.config import get_settings
from milewise.guard import Denied, DynamoStore, Guard, Limits, MemoryStore, Store, question_key
from milewise.obs import emit
from milewise.rag.index import get_rules_index
from milewise.tools.miles import today_jst

STATIC = Path(__file__).parent / "static"


def _charts_version() -> str:
    """Hash of the committed chart manifests: a chart update invalidates cached answers."""
    from milewise.config import data_dir

    h = hashlib.sha256()
    for m in sorted((data_dir() / "charts").glob("*/manifest.yaml")):
        h.update(m.read_bytes())
    return h.hexdigest()[:16]


CHARTS_VERSION = _charts_version()


@cache
def _prompt_sha() -> str:
    from milewise.agent import SYSTEM_PROMPT

    return hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:16]


def needs_openai() -> bool:
    s = get_settings()
    return llm.uses_openai(s.chat_model, s.rerank_model, s.embedding_model)


def load_secrets() -> bool:
    """Resolve Secrets Manager ARNs into env vars; True when everything needed is available.

    Never raises: a secret without a value must not crash the function at init, which
    would also take down /healthz. Callers retry on the next request. The OpenAI key is
    fetched only when some model is configured as openai:* (default: all on Bedrock).
    """
    pairs = {"APP_TOKEN": "APP_TOKEN_SECRET_ARN"}
    if get_settings().turnstile_site_key:
        pairs["TURNSTILE_SECRET"] = "TURNSTILE_SECRET_ARN"
    if needs_openai():
        pairs["OPENAI_API_KEY"] = "OPENAI_API_KEY_SECRET_ARN"
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
    if not load_secrets() or (needs_openai() and not get_settings().openai_api_key):
        raise HTTPException(503, "service not configured yet")


load_secrets()
app = FastAPI(title="Milewise", docs_url=None, redoc_url=None)


@cache
def guard() -> Guard:
    s = get_settings()
    store: Store = DynamoStore(s.quota_table) if s.quota_table else MemoryStore()
    limits = Limits(
        public_requests=s.public_daily_request_limit,
        public_cost_usd=s.public_daily_cost_limit_usd,
        ip_window=s.ip_window_limit,
        ip_daily=s.ip_daily_limit,
        owner_requests=s.daily_request_limit,
        owner_cost_usd=s.daily_cost_limit_usd,
    )
    return Guard(store, limits, alert=_sns_alert if s.alert_topic_arn else None)


def _sns_alert(subject: str, message: str) -> None:
    import boto3

    boto3.client("sns").publish(
        TopicArn=get_settings().alert_topic_arn, Subject=subject[:100], Message=message
    )
    emit("public_budget_alert", subject=subject)


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


def _is_owner(authorization: str | None) -> bool:
    expected = get_settings().app_token
    given = (authorization or "").removeprefix("Bearer ").strip()
    return bool(expected and given) and hmac.compare_digest(given.encode(), expected.encode())


def client_ip(request: Request) -> str:
    """The caller's address as Lambda saw it, never a client-supplied X-Forwarded-For.

    Lambda Web Adapter passes the Function URL request context as a header it sets itself
    (x-amzn-request-context); locally the socket peer is used.
    """
    ctx = request.headers.get("x-amzn-request-context")
    if ctx:
        try:
            return str(json.loads(ctx)["http"]["sourceIp"])
        except (ValueError, KeyError, TypeError):
            pass
    return request.client.host if request.client else "unknown"


def _verify_turnstile(token: str | None, ip: str) -> bool:
    s = get_settings()
    if not s.turnstile_site_key:
        return True  # bot check not configured
    if not token or not s.turnstile_secret:
        return False
    import httpx

    try:
        r = httpx.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={"secret": s.turnstile_secret, "response": token, "remoteip": ip},
            timeout=10,
        )
        return bool(r.json().get("success"))
    except (httpx.HTTPError, ValueError):
        return False


def _cache_key(question: str) -> str:
    s = get_settings()
    index = (data_version().get("rules_index") or {}).get("content_sha256", "")
    # today is part of the key: answers resolve "next July" relative to the date
    return question_key(
        question, today_jst().isoformat(), str(index), CHARTS_VERSION, s.chat_model, _prompt_sha()
    )


def _denied(e: Denied) -> JSONResponse:
    return JSONResponse({"code": e.code, "message": e.message}, status_code=e.status)


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"ok": True, **data_version()}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/config")
def config() -> dict[str, Any]:
    return {"turnstile_site_key": get_settings().turnstile_site_key or None}


@app.post("/ask", response_model=None)
async def ask_endpoint(
    body: AskBody,
    request: Request,
    authorization: str | None = Header(default=None),
    x_turnstile_token: str | None = Header(default=None),
) -> StreamingResponse | JSONResponse:
    await run_in_threadpool(require_secrets)
    g, day = guard(), today_jst().isoformat()
    owner = _is_owner(authorization)
    pool = "owner" if owner else "public"
    ip = client_ip(request)
    request_id = str(uuid.uuid4())
    key = _cache_key(body.question)
    try:
        if not owner:
            if not await run_in_threadpool(g.public_enabled):
                raise Denied(503, "public_off", "the public demo is paused; please try later")
            if not await run_in_threadpool(_verify_turnstile, x_turnstile_token, ip):
                raise Denied(403, "bot_check", "please complete the check and try again")
            await run_in_threadpool(g.check_ip, ip, day)
            hit = await run_in_threadpool(g.cached, key)
            if hit:
                emit("ask", request_id=request_id, cached=1, pool=pool)
                return StreamingResponse(
                    _replay(request_id, hit),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Request-Id": request_id},
                )
        await run_in_threadpool(g.reserve, pool, day)
    except Denied as e:
        emit("ask_denied", request_id=request_id, pool=pool, code=e.code)
        return _denied(e)

    async def stream() -> AsyncIterator[bytes]:
        from milewise.agent import ask

        yield _sse("status", {"request_id": request_id, "stage": "working"})
        t0 = time.perf_counter()
        task = asyncio.ensure_future(run_in_threadpool(ask, body.question))
        # Return as soon as the answer is ready; ping every 3 s meanwhile to keep the
        # stream (and the user) alive. (A plain sleep(3) loop added up to 3 s to every answer.)
        while not task.done():
            done_now, _ = await asyncio.wait({task}, timeout=3)
            if not done_now:
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
        await run_in_threadpool(g.record_cost, pool, day, res.cost_usd)
        sources = sorted(
            {v.get("url") or v.get("chart", {}).get("source_url") for v in res.evidence.values()}
            - {None}
        )
        done = {
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
        }
        yield _sse("answer", {"request_id": request_id, "text": res.answer})
        yield _sse("done", {"request_id": request_id, "cached": False, **done})
        if res.postcheck.ok and not owner:
            # only answers that passed the post-check are reused, and not owner/eval runs
            await run_in_threadpool(
                g.remember, key, json.dumps({"text": res.answer, "done": done}, ensure_ascii=False)
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
            pool=pool,
            tools=[t["name"] for t in res.tool_calls],
            question_sha256=hashlib.sha256(body.question.encode()).hexdigest()[:16],
        )

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Request-Id": request_id},
    )


async def _replay(request_id: str, payload_json: str) -> AsyncIterator[bytes]:
    p = json.loads(payload_json)
    yield _sse("status", {"request_id": request_id, "stage": "cached"})
    yield _sse("answer", {"request_id": request_id, "text": p["text"]})
    yield _sse(
        "done",
        {**p["done"], "request_id": request_id, "cached": True, "cost_usd": 0.0, "latency_ms": 0},
    )
