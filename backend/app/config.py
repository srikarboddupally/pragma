"""Central, typed configuration.

All settings come from environment variables (or a local ``.env``). Loaded once and
cached. Required infrastructure (Postgres, Redis) has localhost defaults so the app boots
in development; external-service credentials default to empty and are only needed when the
corresponding provider/connector is actually used.

Junior note: ``pydantic-settings`` reads env vars case-insensitively, so the env var
``DATABASE_URL`` maps to the field ``database_url`` automatically.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# Embedding vector dimensions per provider. The pgvector column dimension MUST match the
# active provider's model (see CLAUDE.md §4.2). Switching providers => re-embed + migration.
EMBEDDING_DIMS: dict[str, int] = {
    "voyage": 1024,  # voyage-3.5
    "openai": 1536,  # text-embedding-3-small
    "local": 768,  # nomic-embed-text
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- infrastructure (local defaults; override in prod) ---
    database_url: str = "postgresql+asyncpg://pragma:pragma@localhost:5432/pragma"
    redis_url: str = "redis://localhost:6379/0"

    # --- LLM / embeddings ---
    openrouter_api_key: str = ""  # LLM completion, via OpenRouter (default provider)
    llm_model: str = "meta-llama/llama-3.3-70b-instruct:free"  # configurable, not hardcoded
    voyage_api_key: str = ""  # embeddings (default provider)
    openai_api_key: str = ""  # only if embedding_provider == "openai"
    embedding_provider: Literal["voyage", "openai", "local"] = "voyage"

    # --- actions / connectors ---
    stripe_secret_key: str = ""
    slack_client_id: str = ""
    slack_client_secret: str = ""
    github_app_id: str = ""
    github_app_client_id: str = ""
    github_app_private_key_path: str = ""  # path to the App's .pem private key file (read at use)
    notion_client_id: str = ""
    notion_client_secret: str = ""

    # --- security ---
    pragma_encryption_key: str = ""  # Fernet key for credential encryption at rest

    # --- pipeline tuning ---
    max_queue_depth: int = 10_000
    backpressure_sleep_s: float = 1.0

    @property
    def embedding_dim(self) -> int:
        return EMBEDDING_DIMS[self.embedding_provider]


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings singleton.

    Call ``get_settings.cache_clear()`` in tests after mutating the environment.
    """
    return Settings()
