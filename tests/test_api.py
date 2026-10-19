from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from milewise import api
from milewise.agent import AskResult, PostCheck
from milewise.config import get_settings
from milewise.guard import Guard, Limits, MemoryStore, client_key

OWNER = {"Authorization": "Bearer t0ken"}


class Calls:
    def __init__(self) -> None:
        self.asked: list[str] = []
        self.alerts: list[str] = []
        self.histories: list[list[dict[str, str]]] = []


@pytest.fixture
def calls() -> Calls:
    return Calls()


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


def make_guard(store: MemoryStore, calls: Calls, **limits: Any) -> Guard:
    lim = {
        "public_requests": 100,
        "public_cost_usd": 2.0,
        "ip_window": 50,
        "ip_daily": 50,
        **limits,
    }
    return Guard(store, Limits(**lim), alert=lambda subject, msg: calls.alerts.append(subject))


@pytest.fixture
def client(
    store: MemoryStore, calls: Calls, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("APP_TOKEN", "t0ken")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    get_settings.cache_clear()
    monkeypatch.setattr(api, "data_version", lambda: {"rules_index": {"content_sha256": "abc"}})
    monkeypatch.setattr(api, "_prompt_sha", lambda: "p")
    g = make_guard(store, calls)
    monkeypatch.setattr(api, "guard", lambda: g)

    def fake_ask(question: str, on_event: Any = None, history: Any = None) -> AskResult:
        calls.asked.append(question)
        calls.histories.append(history or [])
        if on_event:
            on_event("delta", {"text": "調べます"})  # a preamble, discarded by the tool call
            on_event("tool", {"name": "get_required_miles"})
            on_event("delta", {"text": "110,000"})
            on_event("delta", {"text": "マイル [C1]"})
        return AskResult(
            question=question,
            answer="110,000マイル [C1]",
            evidence={
                "C1": {
                    "origin": {"input": "東京", "city_code": "TYO"},
                    "destination": {"input": "ロンドン", "city_code": "LON"},
                    "cabin": "C",
                    "trip_type": "RT",
                    "chart": {
                        "chart_ids": ["NH-INTL-OWN-RT@2025-06-24"],
                        "booking_from": "2025-06-24",
                        "source_url": "https://www.ana.co.jp/x",
                        "fetched_at": "2026-10-17T00:33:45+09:00",
                    },
                }
            },
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
            model="bedrock:jp.amazon.nova-2-lite-v1:0",
        )

    monkeypatch.setattr("milewise.agent.ask", fake_ask)
    yield TestClient(api.app)
    get_settings.cache_clear()


def use_guard(
    monkeypatch: pytest.MonkeyPatch, store: MemoryStore, calls: Calls, **limits: Any
) -> Guard:
    g = make_guard(store, calls, **limits)
    monkeypatch.setattr(api, "guard", lambda: g)
    return g


def sse_events(text: str) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if not line.startswith(":"))
        if "event" in lines:
            out.append((lines["event"], json.loads(lines["data"])))
    return out


def ask(client: TestClient, q: str, headers: dict[str, str] | None = None) -> Any:
    return client.post("/ask", json={"question": q}, headers=headers or {})


def test_healthz_index_and_config(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"ok": True, "rules_index": {"content_sha256": "abc"}}
    assert "Milewise" in client.get("/").text
    assert client.get("/config").json() == {"turnstile_site_key": None}


def test_public_question_needs_no_token_and_is_counted(
    client: TestClient, store: MemoryStore
) -> None:
    r = ask(client, "東京→ロンドン")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = sse_events(r.text)
    assert [e for e, _ in events] == [
        "status",
        "delta",
        "progress",
        "delta",
        "delta",
        "answer",
        "done",
    ]
    assert events[2][1]["name"] == "get_required_miles"
    assert "".join(d["text"] for e, d in events[3:5]) == events[5][1]["text"]
    assert events[-1][1]["cached"] is False and events[-1][1]["sources"] == [
        "https://www.ana.co.jp/x"
    ]
    [day] = [v for k, v in store.items.items() if k.startswith("public#")]
    assert day["requests"] == 1 and day["cost_usd"] == pytest.approx(0.0003)


def test_done_describes_each_citation(client: TestClient) -> None:
    done = sse_events(ask(client, "東京→ロンドン").text)[-1][1]
    assert done["evidence"] == {"C1": "https://www.ana.co.jp/x"}  # evaluator contract
    assert done["evidence_detail"]["C1"] == {
        "kind": "chart",
        "url": "https://www.ana.co.jp/x",
        "charts": ["NH-INTL-OWN-RT@2025-06-24"],
        "booking_from": "2025-06-24",
        "fetched": "2026-10-17",
        "route": ["TYO", "LON"],
        "cabin": "C",
        "trip_type": "RT",
    }


def test_rule_evidence_card() -> None:
    card = api.evidence_card(
        {
            "title": "払い戻し",
            "url": "https://www.jal.co.jp/c",
            "section": "手数料",
            "fetched_date": "2026-10-17",
        }
    )
    assert card == {
        "kind": "rule",
        "url": "https://www.jal.co.jp/c",
        "title": "払い戻し",
        "section": "手数料",
        "fetched": "2026-10-17",
    }


def test_follow_up_passes_history_and_skips_the_cache(
    client: TestClient, store: MemoryStore, calls: Calls
) -> None:
    ask(client, "那经济舱呢？")  # standalone: cached
    history = [
        {"role": "user", "content": "东京到伦敦商务舱往返要多少里程？"},
        {"role": "assistant", "content": "110,000里程 [C1]"},
    ]
    r = client.post("/ask", json={"question": "那经济舱呢？", "history": history})
    assert sse_events(r.text)[-1][1]["cached"] is False
    assert calls.asked == ["那经济舱呢？", "那经济舱呢？"]  # the follow-up was not a cache hit
    assert calls.histories[-1] == history
    assert not [k for k in store.items if k.startswith("cache#")][1:]  # only the first is stored


def test_history_is_bounded(client: TestClient) -> None:
    turns = [{"role": "user", "content": "q"}] * 13
    assert client.post("/ask", json={"question": "东京", "history": turns}).status_code == 422


def test_same_question_is_answered_from_cache_for_free(
    client: TestClient, store: MemoryStore, calls: Calls
) -> None:
    ask(client, "東京→ロンドン")
    r = ask(client, "  東京→ロンドン ")  # whitespace does not matter
    done = sse_events(r.text)[-1][1]
    assert done["cached"] is True and done["cost_usd"] == 0.0
    assert calls.asked == ["東京→ロンドン"]  # the model ran once
    [day] = [v for k, v in store.items.items() if k.startswith("public#")]
    assert day["requests"] == 1  # the cached answer did not use the site budget


def test_per_ip_window_limit(
    client: TestClient, store: MemoryStore, calls: Calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_guard(monkeypatch, store, calls, ip_window=2)
    assert ask(client, "質問その1").status_code == 200
    assert ask(client, "質問その2").status_code == 200
    r = ask(client, "質問その3")
    assert r.status_code == 429 and r.json()["code"] == "ip_window"
    assert ask(client, "質問その4", OWNER).status_code == 200  # the owner is not limited per IP


def test_ip_comes_from_the_lambda_request_context_not_x_forwarded_for(
    client: TestClient, store: MemoryStore, calls: Calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_guard(monkeypatch, store, calls, ip_window=1)
    ctx = {"x-amzn-request-context": json.dumps({"http": {"sourceIp": "203.0.113.7"}})}
    assert ask(client, "q1", {**ctx, "x-forwarded-for": "198.51.100.1"}).status_code == 200
    # a different forged X-Forwarded-For does not give a fresh allowance
    r = ask(client, "q2", {**ctx, "x-forwarded-for": "198.51.100.2"})
    assert r.status_code == 429
    other = {"x-amzn-request-context": json.dumps({"http": {"sourceIp": "203.0.113.8"}})}
    assert ask(client, "q3", other).status_code == 200


def test_site_budget_closes_the_day_and_emails_once(
    client: TestClient, store: MemoryStore, calls: Calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_guard(monkeypatch, store, calls, public_requests=2)
    assert ask(client, "質問a").status_code == 200
    assert ask(client, "質問b").status_code == 200
    for q in ("質問c", "質問d", "質問e"):
        r = ask(client, q)
        assert r.status_code == 429 and r.json()["code"] == "public_budget"
    assert len(calls.alerts) == 1  # one email per day, not one per refused request
    assert ask(client, "質問f", OWNER).status_code == 200  # owner pool is separate


def test_cost_budget_closes_the_day_first_if_reached_first(
    client: TestClient, store: MemoryStore, calls: Calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_guard(monkeypatch, store, calls, public_cost_usd=0.0005)
    assert ask(client, "質問a").status_code == 200  # $0.0003
    assert ask(client, "質問b").status_code == 200  # $0.0006 now: over
    r = ask(client, "質問c")
    assert r.status_code == 429 and r.json()["code"] == "public_budget" and calls.alerts


def test_kill_switch(
    client: TestClient, store: MemoryStore, calls: Calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    g = use_guard(monkeypatch, store, calls)
    g.set_public(False)
    r = ask(client, "質問a")
    assert r.status_code == 503 and r.json()["code"] == "public_off"
    assert ask(client, "質問a", OWNER).status_code == 200
    g.set_public(True)
    assert ask(client, "質問b").status_code == 200


def test_turnstile_is_required_when_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TURNSTILE_SITE_KEY", "0x4AAAAsite")
    monkeypatch.setenv("TURNSTILE_SECRET", "0x4AAAAsecret")
    get_settings.cache_clear()
    monkeypatch.setattr(api, "_verify_turnstile", lambda token, ip: token == "good")
    assert client.get("/config").json() == {"turnstile_site_key": "0x4AAAAsite"}
    r = ask(client, "質問a", {"x-turnstile-token": "bad"})
    assert r.status_code == 403 and r.json()["code"] == "bot_check"
    assert ask(client, "質問a", {"x-turnstile-token": "good"}).status_code == 200
    assert ask(client, "質問b", OWNER).status_code == 200  # owner skips the bot check


def test_ipv6_is_grouped_by_64() -> None:
    assert client_key("2001:db8:1:2:aaaa::1") == client_key("2001:db8:1:2:bbbb::9")
    assert client_key("2001:db8:1:3::1") != client_key("2001:db8:1:2::1")
    assert client_key("203.0.113.7") == "203.0.113.7" and client_key("nonsense") == "unknown"


def test_missing_openai_key_only_matters_for_openai_models(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    get_settings.cache_clear()
    assert ask(client, "東京→ロンドン").status_code == 200  # all on Bedrock by default
    monkeypatch.setenv("CHAT_MODEL", "openai:gpt-6-luna")
    get_settings.cache_clear()
    assert client.get("/healthz").status_code == 200
    assert ask(client, "東京→ロンドン2").status_code == 503
