"""Model access behind one naming scheme: "<provider>:<model id>" (design.md §4.7).

    bedrock:jp.amazon.nova-2-lite-v1:0     Amazon Bedrock (Converse API), billed by AWS
    openai:gpt-6-luna                      OpenAI API (kept for A/B comparisons)

A bare name without a known prefix is treated as OpenAI, so old configs keep working.
Three call shapes cover everything Milewise does:
  chat_model()  a LangChain chat model for the tool-calling agent
  structured()  one call that must return JSON matching a schema (reranker, eval judge)
  embed()       text embeddings for the rules index and queries
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from functools import cache
from typing import Any

from milewise.config import Settings, get_settings

PROVIDERS = ("bedrock", "openai")
EMBED_BATCH = 64


def parse(model: str) -> tuple[str, str]:
    provider, sep, rest = model.partition(":")
    if sep and provider in PROVIDERS:
        return provider, rest
    return "openai", model


def model_id(model: str) -> str:
    return parse(model)[1]


def uses_openai(*models: str) -> bool:
    return any(parse(m)[0] == "openai" for m in models)


@cache
def _bedrock(region: str) -> Any:
    import boto3
    from botocore.config import Config

    return boto3.client(
        "bedrock-runtime",
        region_name=region,
        # Bounded: a stuck call must fail fast, not stall a request or an eval run for
        # minutes (6 attempts x 120 s once hung a local eval for an hour, §9.15).
        config=Config(retries={"max_attempts": 3, "mode": "standard"}, read_timeout=60),
    )


def _openai(s: Settings) -> Any:
    from openai import OpenAI

    return OpenAI(api_key=s.openai_api_key or None, max_retries=2, timeout=90)


def chat_model(model: str, s: Settings | None = None, max_tokens: int = 4000) -> Any:
    s = s or get_settings()
    provider, mid = parse(model)
    if provider == "bedrock":
        from langchain_aws import ChatBedrockConverse

        # reasoningConfig is Nova 2's own field; other models reject it (Nova Pro does too)
        extra = (
            {"reasoningConfig": {"type": "enabled", "maxReasoningEffort": s.reasoning_effort}}
            if "nova-2" in mid
            else None
        )
        return ChatBedrockConverse(
            model=mid,
            client=_bedrock(s.bedrock_region),
            max_tokens=max_tokens,
            additional_model_request_fields=extra,
            # langchain-aws only streams providers it has verified (not zai, for GLM-5); the
            # Converse stream itself works for GLM-5 with tool use (checked 2026-10-21).
            disable_streaming=False,
        )
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=mid,
        api_key=s.openai_api_key or None,  # type: ignore[arg-type]
        use_responses_api=True,
        reasoning={"effort": s.reasoning_effort},
        max_tokens=max_tokens,  # type: ignore[call-arg]
    )


def structured(
    model: str,
    *,
    name: str,
    instructions: str,
    prompt: str,
    schema: dict[str, Any],
    s: Settings | None = None,
    max_tokens: int = 2000,
) -> tuple[dict[str, Any], dict[str, int]]:
    """One call whose answer is a JSON object matching `schema`; returns (object, usage).

    OpenAI: structured outputs. Bedrock: a single tool the model is forced to call, whose
    input is the object (Converse has no response_format; verified on Nova 2 Lite).
    """
    s = s or get_settings()
    provider, mid = parse(model)
    if provider == "bedrock":
        r = _bedrock(s.bedrock_region).converse(
            modelId=mid,
            system=[{"text": instructions}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            toolConfig={
                "tools": [
                    {
                        "toolSpec": {
                            "name": name,
                            "description": "Return the result in this exact structure.",
                            "inputSchema": {"json": schema},
                        }
                    }
                ],
                "toolChoice": {"tool": {"name": name}},
            },
            inferenceConfig={"maxTokens": max_tokens},
        )
        out = next(
            c["toolUse"]["input"] for c in r["output"]["message"]["content"] if "toolUse" in c
        )
        u = r.get("usage", {})
        return dict(out), {
            "input_tokens": u.get("inputTokens", 0),
            "cached_input_tokens": u.get("cacheReadInputTokens", 0),
            "output_tokens": u.get("outputTokens", 0),
        }
    resp = _openai(s).responses.create(
        model=mid,
        instructions=instructions,
        input=prompt,
        text={"format": {"type": "json_schema", "name": name, "schema": schema, "strict": True}},
        reasoning={"effort": s.reasoning_effort},
        max_output_tokens=max_tokens,
    )
    usage = (
        {
            "input_tokens": resp.usage.input_tokens,
            "cached_input_tokens": resp.usage.input_tokens_details.cached_tokens,
            "output_tokens": resp.usage.output_tokens,
        }
        if resp.usage
        else {}
    )
    return json.loads(resp.output_text), usage


def embed(
    texts: Sequence[str],
    model: str | None = None,
    s: Settings | None = None,
    kind: str = "document",
) -> list[list[float]]:
    """kind: "document" for indexed chunks, "query" for questions (Cohere embeds them
    differently; Titan and OpenAI ignore it)."""
    s = s or get_settings()
    provider, mid = parse(model or s.embedding_model)
    if provider == "bedrock" and mid.startswith("cohere."):
        return _embed_cohere(texts, mid, s, kind)
    if provider == "bedrock":
        client = _bedrock(s.bedrock_region)
        out = []
        for text in texts:  # Titan Text Embeddings V2 takes one input per request
            r = client.invoke_model(
                modelId=mid,
                body=json.dumps(
                    {"inputText": text, "dimensions": s.embedding_dimensions, "normalize": True}
                ),
            )
            out.append(json.loads(r["body"].read())["embedding"])
        return out
    client = _openai(s)
    result: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        r = client.embeddings.create(
            model=mid, input=list(texts[i : i + EMBED_BATCH]), dimensions=s.embedding_dimensions
        )
        result.extend(d.embedding for d in sorted(r.data, key=lambda d: d.index))
    return result


COHERE_BATCH = 96  # Cohere on Bedrock: up to 96 texts per request


def _embed_cohere(texts: Sequence[str], mid: str, s: Settings, kind: str) -> list[list[float]]:
    client = _bedrock(s.bedrock_region)
    out: list[list[float]] = []
    for i in range(0, len(texts), COHERE_BATCH):
        body: dict[str, Any] = {
            "texts": list(texts[i : i + COHERE_BATCH]),
            "input_type": "search_query" if kind == "query" else "search_document",
            "embedding_types": ["float"],
            "truncate": "END",
        }
        if "v4" in mid:
            body["output_dimension"] = s.embedding_dimensions
        r = client.invoke_model(modelId=mid, body=json.dumps(body))
        emb = json.loads(r["body"].read())["embeddings"]
        out.extend(emb["float"] if isinstance(emb, dict) else emb)
    return out
