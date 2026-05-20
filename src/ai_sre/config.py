"""Application configuration.

All settings are sourced from environment variables prefixed with `AI_SRE_`.
Defaults are appropriate for local development; production deployments must
override security-sensitive values.

Adding a new setting:
    1. Add it as a field on one of the *Settings classes below.
    2. Add a one-line docstring as the `description` parameter.
    3. Add it to `.env.example` with a safe default.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Settings root. All env vars start with AI_SRE_."""

    model_config = SettingsConfigDict(
        env_prefix="AI_SRE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- App ----
    env: Literal["local", "dev", "staging", "prod"] = Field(
        default="local", description="Deployment environment."
    )
    debug: bool = Field(default=False, description="Enable debug mode (FastAPI/ASGI).")
    host: str = Field(default="0.0.0.0", description="Bind host for the API.")
    port: int = Field(default=8000, description="Bind port for the API.")

    # ---- Database ----
    db_url: str = Field(
        default="postgresql+asyncpg://aisre:aisre@localhost:5432/aisre",
        description="SQLAlchemy URL for the primary Postgres.",
    )
    db_pool_size: int = Field(default=10, description="DB connection pool size.")

    # ---- Security ----
    admin_token: SecretStr = Field(
        default=SecretStr("change-me"),
        description="Shared secret for admin-only endpoints (e.g. tenant creation).",
    )
    tenant_encryption_key: SecretStr = Field(
        default=SecretStr("change-me-32-bytes-of-base64-yyy="),
        description="Base64-encoded 32-byte key for envelope-encrypting tenant secrets.",
    )

    # ---- LLM ----
    llm_provider: Literal["anthropic"] = Field(
        default="anthropic", description="LLM provider for the gateway."
    )
    llm_model: str = Field(
        default="claude-opus-4-7", description="Model identifier for the gateway."
    )
    llm_api_key: SecretStr = Field(
        default=SecretStr(""), description="API key for the LLM provider."
    )
    llm_max_tokens_per_investigation: int = Field(
        default=200_000, description="Hard cap on tokens per investigation."
    )
    llm_max_cost_usd_per_investigation: float = Field(
        default=0.50, description="Hard cap on USD spend per investigation."
    )

    # ---- Investigation budgets ----
    inv_budget_wall_seconds: int = Field(
        default=300, description="Wall-clock budget per investigation."
    )
    inv_budget_tool_calls: int = Field(
        default=30, description="Max tool calls per investigation."
    )
    inv_dedupe_window_seconds: int = Field(
        default=900, description="Window for alert deduplication."
    )

    # ---- Queue (Procrastinate) ----
    queue_concurrency: int = Field(
        default=10,
        description="Max concurrent jobs per worker process.",
    )
    queue_default_retries: int = Field(
        default=3, description="Default retry count for queue tasks."
    )

    # ---- Prometheus connector ----
    prom_query_timeout_seconds: int = Field(
        default=10, description="Default query timeout for Prometheus."
    )
    prom_max_points: int = Field(
        default=10_000, description="Max data points returned per query."
    )
    prom_max_series: int = Field(default=10_000, description="Max series per query.")

    # ---- Slack ----
    slack_client_id: str = Field(default="", description="Slack OAuth client ID.")
    slack_client_secret: SecretStr = Field(
        default=SecretStr(""), description="Slack OAuth client secret."
    )
    slack_signing_secret: SecretStr = Field(
        default=SecretStr(""), description="Slack request signing secret."
    )


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Cached settings accessor. Safe to call from anywhere."""
    return AppSettings()
