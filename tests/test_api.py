from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from milewise import api
from milewise.agent import AskResult, PostCheck
from milewise.config import get_settings
from milewise.db import Db


@pytest.fixture
def client(db: Db, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("APP_TOKEN", "t0ken")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("DAILY_REQUEST_LIMIT", "2")
    get_settings.cache_clear()
    monkeypatch.setattr(api, "_db", db)
    db.execute("DELETE FROM ops.daily_usage")

    def fake_ask(question: str, _db: Any) -> AskResult:
        return AskResult(
            question=question,
            answer="110,000マイル [C1]",
            evidence={"C1": {"chart": {"source_url": "https://www.ana.co.jp/x"}}},
            postcheck=PostCheck(True, [], [], []),
            tool_calls=[{"name": "get_required_miles", "args": {}}],
            usage={
                "input_tokens": 10,
                "output_tokens": 5,
                "cached_input_tokens": 0,
                "model_calls": 1,
            },
            cost_usd=0.0003,
            latency_ms=12,
            model="gpt-6-luna",
        )

    monkeypatch.setattr("milewise.agent.ask", fake_ask)
    yield TestClient(api.app)
    get_settings.cache_clear()


def sse_events(text: str) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if not line.startswith(":"))
        if "event" in lines:
            out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_healthz_and_index(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"ok": True}
    assert "Milewise" in client.get("/").text


def test_ask_requires_token(client: TestClient) -> None:
    assert client.post("/ask", json={"question": "東京→ロンドン"}).status_code == 401
    r = client.post(
        "/ask", json={"question": "東京→ロンドン"}, headers={"Authorization": "Bearer nope"}
    )
    assert r.status_code == 401


def test_ask_streams_status_answer_done_and_records_cost(client: TestClient, db: Db) -> None:
    r = client.post(
        "/ask", json={"question": "東京→ロンドン"}, headers={"Authorization": "Bearer t0ken"}
    )
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = sse_events(r.text)
    assert [e for e, _ in events] == ["status", "answer", "done"]
    assert events[1][1]["text"] == "110,000マイル [C1]"
    assert events[2][1]["sources"] == ["https://www.ana.co.jp/x"]
    usage = db.query("SELECT requests, cost_usd FROM ops.daily_usage")[0]
    assert usage["requests"] == 1 and float(usage["cost_usd"]) == pytest.approx(0.0003)


def test_daily_quota(client: TestClient) -> None:
    h = {"Authorization": "Bearer t0ken"}
    assert client.post("/ask", json={"question": "質問その1"}, headers=h).status_code == 200
    assert client.post("/ask", json={"question": "質問その2"}, headers=h).status_code == 200
    assert client.post("/ask", json={"question": "質問その3"}, headers=h).status_code == 429


def test_events_reports_partial_batch_failures(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_ingest(event: dict[str, Any]) -> None:
        if event.get("fail"):
            raise RuntimeError("bad object")

    monkeypatch.setattr(api, "_ingest_s3_event", fake_ingest)
    batch = {
        "Records": [
            {"messageId": "m1", "body": json.dumps({"Records": []})},
            {"messageId": "m2", "body": json.dumps({"fail": True})},
        ]
    }
    assert client.post("/events", json=batch).json() == {
        "batchItemFailures": [{"itemIdentifier": "m2"}]
    }


def test_missing_secret_degrades_instead_of_crashing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    get_settings.cache_clear()
    assert client.get("/healthz").status_code == 200
    r = client.post(
        "/ask", json={"question": "東京→ロンドン"}, headers={"Authorization": "Bearer t0ken"}
    )
    assert r.status_code == 503
