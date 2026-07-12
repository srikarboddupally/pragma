"""Shared test fixtures.

Sets a complete fake environment BEFORE any app module is imported, so settings load with
deterministic test values. External HTTP services are never hit in tests.

DB-correctness tests use a REAL ephemeral Postgres (pgvector image) via testcontainers — you
cannot test SERIALIZABLE conflicts, pgvector search, or RLS against a mock. These fixtures
skip cleanly when Docker / testcontainers is unavailable, so the unit suite stays green.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

# --- Fake environment (must run before importing app.config) ---
_TEST_ENV = {
    "DATABASE_URL": "postgresql+asyncpg://pragma:pragma@localhost:5432/pragma_test",
    "REDIS_URL": "redis://localhost:6379/1",
    "OPENROUTER_API_KEY": "sk-or-test-key",
    "VOYAGE_API_KEY": "voyage-test-key",
    "EMBEDDING_PROVIDER": "voyage",
    "STRIPE_SECRET_KEY": "sk_test_dummy",
    "PRAGMA_ENCRYPTION_KEY": "dGVzdC1mZXJuZXQta2V5LTMyLWJ5dGVzLWxvbmchISE=",
}
for _k, _v in _TEST_ENV.items():
    os.environ.setdefault(_k, _v)

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    """An ephemeral pgvector Postgres connection URL, or skip if Docker is unavailable."""
    try:
        from testcontainers.postgres import PostgresContainer
    except Exception:  # noqa: BLE001
        pytest.skip("testcontainers not installed")
    try:
        with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
            yield pg.get_connection_url()
    except Exception as exc:  # noqa: BLE001  (Docker not running, image pull blocked, etc.)
        pytest.skip(f"Docker/Postgres unavailable: {exc}")


@pytest.fixture(scope="session")
def migrated_pg_url(pg_url: str) -> str:
    """Run all Alembic migrations against the ephemeral Postgres and return its URL."""
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", pg_url)  # env.py honors a preset URL
    command.upgrade(cfg, "head")
    return pg_url
