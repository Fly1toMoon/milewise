from __future__ import annotations

from typing import Any

import pytest

from milewise import llm
from milewise.config import Settings


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("bedrock:jp.amazon.nova-2-lite-v1:0", ("bedrock", "jp.amazon.nova-2-lite-v1:0")),
        ("openai:gpt-6-luna", ("openai", "gpt-6-luna")),
        ("gpt-6-luna", ("openai", "gpt-6-luna")),  # bare names keep meaning OpenAI
    ],
)
def test_parse(model: str, expected: tuple[str, str]) -> None:
    assert llm.parse(model) == expected


class FakeBedrock:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    def converse(self, **kw: Any) -> dict[str, Any]:
        self.kwargs = kw
        return {
            "output": {
                "message": {
                    "content": [
                        {"reasoningContent": {}},
                        {"toolUse": {"name": "rerank", "input": {"relevant": [2, 0]}}},
                    ]
                }
            },
            "usage": {"inputTokens": 900, "outputTokens": 20},
        }


def test_structured_on_bedrock_forces_the_schema_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeBedrock()
    monkeypatch.setattr(llm, "_bedrock", lambda region: fake)
    out, usage = llm.structured(
        "bedrock:jp.amazon.nova-2-lite-v1:0",
        name="rerank",
        instructions="pick",
        prompt="q",
        schema={"type": "object"},
        s=Settings(),
    )
    assert out == {"relevant": [2, 0]}
    assert usage == {"input_tokens": 900, "cached_input_tokens": 0, "output_tokens": 20}
    assert fake.kwargs["toolConfig"]["toolChoice"] == {"tool": {"name": "rerank"}}
    assert fake.kwargs["modelId"] == "jp.amazon.nova-2-lite-v1:0"
