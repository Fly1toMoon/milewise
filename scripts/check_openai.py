"""Preflight for design.md R4: can this OpenAI project key do everything Milewise needs?

Checks, with one tiny request each (total well under $0.001):
  1. the chat model is visible to this key
  2. Responses API function calling (the agent depends on it)
  3. structured outputs (the router depends on it)
  4. embeddings at the configured dimensions, plus a cross-lingual sanity check

Reads OPENAI_API_KEY and optional OPENAI_MODEL / OPENAI_EMBEDDING_MODEL / EMBEDDING_DIMENSIONS.
"""

from __future__ import annotations

import json
import math
import os
import sys
from collections.abc import Callable
from typing import Any

import openai
from openai import OpenAI

MODEL = os.environ.get("OPENAI_MODEL", "gpt-6-luna")
EMBEDDING_MODELS = [
    os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-large"),
    "text-embedding-3-small",
]
DIMENSIONS = int(os.environ.get("EMBEDDING_DIMENSIONS", "1024"))

MILES_TOOL = {
    "type": "function",
    "name": "get_required_miles",
    "description": "Look up required award miles from the airline's published award chart.",
    "strict": True,
    "parameters": {
        "type": "object",
        "properties": {
            "airline": {"type": "string", "enum": ["NH", "JL"]},
            "origin": {"type": "string"},
            "destination": {"type": "string"},
            "cabin": {"type": "string", "enum": ["Y", "PY", "C", "F"]},
            "trip_type": {"type": "string", "enum": ["RT", "OW"]},
        },
        "required": ["airline", "origin", "destination", "cabin", "trip_type"],
        "additionalProperties": False,
    },
}

ROUTER_SCHEMA = {
    "type": "object",
    "properties": {
        "lang": {"type": "string", "enum": ["ja", "en", "zh"]},
        "intent": {"type": "string", "enum": ["miles", "rules", "both", "out_of_scope"]},
        "ja_query": {"type": "string"},
    },
    "required": ["lang", "intent", "ja_query"],
    "additionalProperties": False,
}


def check(name: str, fn: Callable[[OpenAI], str], client: OpenAI) -> bool:
    try:
        print(f"[ok]   {name}: {fn(client)}")
        return True
    except openai.APIStatusError as e:
        print(f"[FAIL] {name}: HTTP {e.status_code} {e.message}")
    except openai.APIConnectionError as e:
        print(f"[FAIL] {name}: connection error: {e}")
    except (AssertionError, KeyError, ValueError) as e:
        print(f"[FAIL] {name}: {e}")
    return False


def usage(r: Any) -> str:
    return f"in={r.usage.input_tokens} out={r.usage.output_tokens}"


def model_visible(c: OpenAI) -> str:
    return f"{c.models.retrieve(MODEL).id} is visible to this key"


def tool_calling(c: OpenAI) -> str:
    r = c.responses.create(
        model=MODEL,
        input="東京→ロンドンのビジネスクラス往復、ANAの特典航空券は何マイル？",
        tools=[MILES_TOOL],
        reasoning={"effort": "low"},
        max_output_tokens=2000,
    )
    calls = [o for o in r.output if o.type == "function_call"]
    assert calls, f"expected a function_call, got output types {[o.type for o in r.output]}"
    args = json.loads(calls[0].arguments)
    assert args["airline"] == "NH" and args["cabin"] == "C", f"unexpected args {args}"
    return f"{calls[0].name}({args}) {usage(r)}"


def structured_output(c: OpenAI) -> str:
    r = c.responses.create(
        model=MODEL,
        input="特典机票取消的话手续费是多少？里程会退回吗？",
        instructions="Classify the question and rewrite it as a Japanese search query.",
        text={
            "format": {
                "type": "json_schema",
                "name": "router",
                "schema": ROUTER_SCHEMA,
                "strict": True,
            }
        },
        reasoning={"effort": "low"},
        max_output_tokens=2000,
    )
    out = json.loads(r.output_text)
    # Capability check only: schema-valid JSON and the right language. Whether the
    # intent label is right is a router-prompt question, measured by the eval (M3).
    assert set(out) == set(ROUTER_SCHEMA["required"]), f"keys {sorted(out)}"
    assert out["intent"] in ROUTER_SCHEMA["properties"]["intent"]["enum"], out
    assert out["lang"] == "zh", f"expected lang=zh, got {out}"
    return f"{out} {usage(r)}"


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def embeddings(model: str) -> Callable[[OpenAI], str]:
    def run(c: OpenAI) -> str:
        texts = [
            "特典航空券の払い戻し手数料",  # ja
            "奖励机票退票手续费是多少",  # zh, same meaning
            "東京の明日の天気",  # ja, unrelated
        ]
        r = c.embeddings.create(model=model, input=texts, dimensions=DIMENSIONS)
        vecs = [d.embedding for d in r.data]
        assert len(vecs[0]) == DIMENSIONS, f"dim {len(vecs[0])} != {DIMENSIONS}"
        related, unrelated = cosine(vecs[0], vecs[1]), cosine(vecs[0], vecs[2])
        assert related > unrelated, f"cross-lingual sanity failed: {related:.3f} <= {unrelated:.3f}"
        return f"dim={DIMENSIONS} ja~zh={related:.3f} > ja~unrelated={unrelated:.3f}"

    return run


def main() -> int:
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set (put it in .env, see .env.example)")
        return 1
    client = OpenAI(max_retries=2, timeout=60)
    results = [
        check(f"model {MODEL}", model_visible, client),
        check("Responses API function calling", tool_calling, client),
        check("structured outputs", structured_output, client),
        *(check(f"embeddings {m}", embeddings(m), client) for m in EMBEDDING_MODELS),
    ]
    print(f"\n{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
