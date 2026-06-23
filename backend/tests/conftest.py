"""Shared test fixtures.

Sets a complete fake environment BEFORE any app module is imported, so settings load with
deterministic test values. External HTTP services are never hit in tests — provider/connector
mocks are added in later phases as those modules appear.

The real ephemeral Postgres fixture (testcontainers) is defined here but not autouse; only
DB-correctness tests (Phase 1+) request it, and it requires Docker to be running.
"""

from __future__ import annotations

import os

# --- Fake environment (must run before importing app.config) ---
_TEST_ENV = {
    "DATABASE_URL": "postgresql+asyncpg://pragma:pragma@localhost:5432/pragma_test",
    "REDIS_URL": "redis://localhost:6379/1",
    "ANTHROPIC_API_KEY": "sk-ant-test-key",
    "VOYAGE_API_KEY": "voyage-test-key",
    "EMBEDDING_PROVIDER": "voyage",
    "STRIPE_SECRET_KEY": "sk_test_dummy",
    "PRAGMA_ENCRYPTION_KEY": "dGVzdC1mZXJuZXQta2V5LTMyLWJ5dGVzLWxvbmchISE=",
}
for _k, _v in _TEST_ENV.items():
    os.environ.setdefault(_k, _v)

from app.config import get_settings  # noqa: E402

# Ensure settings reflect the test environment even if imported earlier elsewhere.
get_settings.cache_clear()
