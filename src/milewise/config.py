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

    openai_api_key: str = ""
    openai_model: str = "gpt-6-luna"
    openai_strong_model: str = "gpt-6.1-sol"
    openai_reasoning_effort: str = "low"
    openai_embedding_model: str = "text-embedding-3-large"
    embedding_dimensions: int = 1024

    database_url: str = "postgresql://milewise:milewise@localhost:5432/milewise"
    # Set in Lambda: Aurora through the RDS Data API instead of database_url.
    db_cluster_arn: str = ""
    db_secret_arn: str = ""
    db_name: str = "milewise"

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


# USD per 1M tokens (input, cached input, output), from the official model pages
# (checked 2026-10-17). Used for per-request cost logging and eval cost (design.md §6).
PRICES: dict[str, tuple[float, float, float]] = {
    "gpt-6-luna": (0.10, 0.01, 0.50),
    "gpt-6.1-sol": (2.00, 0.10, 10.00),
}


def cost_usd(model: str, input_tokens: int, cached_input_tokens: int, output_tokens: int) -> float:
    if model not in PRICES:
        raise KeyError(f"no price for model {model!r}: add it to milewise.config.PRICES")
    p_in, p_cached, p_out = PRICES[model]
    uncached = input_tokens - cached_input_tokens
    return (uncached * p_in + cached_input_tokens * p_cached + output_tokens * p_out) / 1_000_000
