import json

import httpx
import pytest

from milewise.evals.grade import cited_urls
from milewise.evals.remote import ask_remote


def sse(*events: tuple[str, dict[str, object]]) -> str:
    return (
        "".join(f"event: {e}\ndata: {json.dumps(d, ensure_ascii=False)}\n\n" for e, d in events)
        + ": ping\n\n"
    )


def client_returning(body: str, status: int = 200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer tok"
        assert json.loads(request.content) == {"question": "東京→ロンドン"}
        return httpx.Response(status, text=body, headers={"content-type": "text/event-stream"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_remote_answer_is_parsed_into_ask_result() -> None:
    body = sse(
        ("status", {"stage": "working"}),
        ("answer", {"text": "110,000マイル [C1]"}),
        (
            "done",
            {
                "evidence": {"C1": "https://www.ana.co.jp/x"},
                "tools": ["get_required_miles"],
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "postcheck_ok": True,
                "cost_usd": 0.0004,
                "model": "gpt-6-luna",
            },
        ),
    )
    res = ask_remote(
        "https://candidate.example/", "tok", "東京→ロンドン", client=client_returning(body)
    )
    assert res.answer == "110,000マイル [C1]" and res.postcheck.ok and res.cost_usd == 0.0004
    assert [t["name"] for t in res.tool_calls] == ["get_required_miles"]
    assert cited_urls(res) == ["https://www.ana.co.jp/x"]  # citation check works remotely too


def test_incomplete_stream_fails_loudly() -> None:
    body = sse(("status", {"stage": "working"}), ("error", {"message": "internal error"}))
    with pytest.raises(RuntimeError, match="incomplete"):
        ask_remote("https://c.example", "tok", "東京→ロンドン", client=client_returning(body))


def test_http_error_is_raised() -> None:
    with pytest.raises(httpx.HTTPStatusError):
        ask_remote("https://c.example", "tok", "東京→ロンドン", client=client_returning("", 429))
