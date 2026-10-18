"""Runtime settings, read from environment variables (and .env for local use)."""

from __future__ import annotations

import os
from functools import cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    """Committed data (charts, locations). Overridable for the Lambda image."""
    return Path(os.environ.get("MILEWISE_DATA_DIR", _REPO_ROOT / "data"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Models as "<provider>:<id>" (milewise.llm). Defaults run entirely on Amazon Bedrock,
    # billed to the AWS account (credits apply); OpenAI stays available for A/B runs.
    chat_model: str = "bedrock:jp.amazon.nova-2-lite-v1:0"  # agent + final answer
    rerank_model: str = "bedrock:jp.amazon.nova-2-lite-v1:0"  # search_rules relevance filter
    # A different model family from the answering one, so the judge is not grading itself (R10).
    judge_model: str = "bedrock:apac.amazon.nova-pro-v1:0"
    embedding_model: str = "bedrock:amazon.titan-embed-text-v2:0"
    embedding_dimensions: int = 1024
    reasoning_effort: str = "low"
    bedrock_region: str = "ap-northeast-1"
    openai_api_key: str = ""  # only needed when a model above is openai:*

    # DynamoDB table for the daily quota (set in Lambda); empty = in-process counter.
    usage_table: str = ""

    # Public endpoint guards (design.md R7). In Lambda the secrets come from Secrets
    # Manager ARNs and are resolved once at start-up (milewise.api.load_secrets).
    app_token: str = ""
    app_token_secret_arn: str = ""
    openai_api_key_secret_arn: str = ""
    daily_request_limit: int = 100
    daily_cost_limit_usd: float = 1.0


@cache
def get_settings() -> Settings:
    return Settings()


# USD per 1M tokens (input, cached input, output). Used for per-request cost logging and
# eval cost (design.md §6). OpenAI: official model pages (2026-10-17). Bedrock: AWS Price
# List API, ap-northeast-1 on-demand (2026-10-18); the jp. geo profile bills at the Tokyo
# regional rate, the apac. profile (Nova Pro) at the source region's rate.
PRICES: dict[str, tuple[float, float, float]] = {
    "gpt-6-luna": (0.10, 0.01, 0.50),
    "gpt-6.1-sol": (2.00, 0.10, 10.00),
    "jp.amazon.nova-2-lite-v1:0": (0.396, 0.099, 3.311),
    "global.amazon.nova-2-lite-v1:0": (0.36, 0.09, 3.01),
    "apac.amazon.nova-pro-v1:0": (0.96, 0.24, 3.84),
}


def cost_usd(model: str, input_tokens: int, cached_input_tokens: int, output_tokens: int) -> float:
    provider, sep, rest = model.partition(":")
    if sep and provider in ("bedrock", "openai"):
        model = rest
    if model not in PRICES:
        raise KeyError(f"no price for model {model!r}: add it to milewise.config.PRICES")
    p_in, p_cached, p_out = PRICES[model]
    uncached = input_tokens - cached_input_tokens
    return (uncached * p_in + cached_input_tokens * p_cached + output_tokens * p_out) / 1_000_000
