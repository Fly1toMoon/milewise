"""HTTP entry point: the same app runs in Lambda (via Lambda Web Adapter) and locally.

    uv run --env-file .env uvicorn milewise.api:app --port 8080

Routes
  GET  /         single-page UI
  GET  /healthz  readiness check for the adapter
  POST /ask      Bearer token, daily quota, Server-Sent Events: status → answer → done
  POST /events   SQS batch (S3 ObjectCreated) forwarded by the adapter → ingestion
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote_plus

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from milewise.config import data_dir, get_settings
from milewise.db import Db, connect
from milewise.obs import emit
from milewise.tools.miles import today_jst

STATIC = Path(__file__).parent / "static"
_RAW_KEY = re.compile(r"(?:^|/)(?P<airline>nh|jl)/(?P<id>[a-z0-9-]+)_(?P<ts>\d{8}T\d{6})\.html$")


def load_secrets() -> bool:
    """Resolve Secrets Manager ARNs into env vars; True when everything is available.

    Never raises: a secret without a value (e.g. the OpenAI key before `make
    put-openai-key`) must not crash the function at init, which would also take down
    /healthz and SQS ingestion. Callers retry on the next request (seen on the first
    `make up`: S3's test event reached the ingest function before the key was stored).
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
_db: Db | None = None


def db() -> Db:
    global _db
    if _db is None:
        _db = connect()
    return _db


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
    row = db().query(
        "INSERT INTO ops.daily_usage (day, requests) VALUES (CAST(:d AS date), 1)"
        " ON CONFLICT (day) DO UPDATE SET requests = ops.daily_usage.requests + 1"
        " RETURNING requests, cost_usd",
        {"d": today_jst().isoformat()},
    )[0]
    if row["requests"] > s.daily_request_limit or float(row["cost_usd"]) > s.daily_cost_limit_usd:
        raise HTTPException(429, "daily limit reached; try again tomorrow")


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}


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
        task = asyncio.ensure_future(run_in_threadpool(ask, body.question, db()))
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
        await run_in_threadpool(
            db().execute,
            "UPDATE ops.daily_usage SET cost_usd = cost_usd + :c WHERE day = CAST(:d AS date)",
            {"c": res.cost_usd, "d": today_jst().isoformat()},
        )
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
                "postcheck_ok": res.postcheck.ok,
                "latency_ms": res.latency_ms,
                "cost_usd": res.cost_usd,
                "model": res.model,
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


@app.post("/events")
async def events(request: Request) -> JSONResponse:
    """SQS → ingest. Returns partial-batch failures so only failed messages are retried."""
    await run_in_threadpool(require_secrets)  # 503 → every message is retried later
    payload = await request.json()
    failures = []
    for record in payload.get("Records", []):
        try:
            await run_in_threadpool(_ingest_s3_event, json.loads(record["body"]))
        except Exception as e:
            emit(
                "ingest",
                service="ingest",
                error=1,
                message_id=record.get("messageId"),
                error_type=type(e).__name__,
                detail=str(e)[:500],
            )
            failures.append({"itemIdentifier": record["messageId"]})
    return JSONResponse({"batchItemFailures": failures})


def _ingest_s3_event(s3_event: dict[str, Any]) -> None:
    import boto3
    import yaml

    from milewise.rag.ingest import ingest_page

    sources = {
        s["id"]: s
        for s in yaml.safe_load((data_dir() / "sources.yaml").read_text(encoding="utf-8"))[
            "sources"
        ]
    }
    s3 = boto3.client("s3")
    for rec in s3_event.get("Records", []):  # S3 test events have no Records
        bucket, key = rec["s3"]["bucket"]["name"], unquote_plus(rec["s3"]["object"]["key"])
        m = _RAW_KEY.search(key)
        if not m or m["id"] not in sources or sources[m["id"]]["kind"] != "rules":
            emit("ingest", service="ingest", skipped=key)
            continue
        obj = s3.get_object(Bucket=bucket, Key=key)
        fetched = datetime.strptime(m["ts"], "%Y%m%dT%H%M%S").isoformat() + "+09:00"
        res = ingest_page(
            db(),
            obj["Body"].read().decode("utf-8"),
            source=sources[m["id"]],
            raw_key=f"s3://{bucket}/{key}",
            raw_etag=obj["ETag"].strip('"'),
            fetched_at=fetched,
            embedding_model=get_settings().openai_embedding_model,
        )
        emit("ingest", service="ingest", key=key, status=res.status, chunks=res.chunks_written)
