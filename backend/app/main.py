"""FastAPI application factory (hot path).

Phase 0 + retrofits: a liveness/readiness health split and OpenTelemetry FastAPI
instrumentation. Feature routers (search, skill, ask, actions, approvals, audit, sources) are
added in later phases. Hard rule: nothing mounted here may trigger cold-path work (Celery,
embedding new docs, extraction).

Observability: ``FastAPIInstrumentor`` creates per-request trace context now, but **no**
exporter/``TracerProvider`` is configured — spans attach to the global no-op provider until an
exporter is wired in v2 (CLAUDE.md §4.11). The trace context exists from day 1; shipping it is
the v2 addition.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from sqlalchemy import text

from app.config import get_settings
from app.db.session import engine
from app.logging import configure_logging


async def _check_db() -> bool:
    """Return True if the database is reachable (cheap ``SELECT 1``)."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        # A readiness probe reports "down" on any failure; it never raises.
        return False


async def _check_redis() -> bool:
    """Return True if Redis is reachable (``PING``)."""
    from redis.asyncio import from_url

    try:
        async with from_url(get_settings().redis_url) as client:
            await client.ping()
        return True
    except Exception:
        return False


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="Pragma API", version="0.1.0")

    @app.get("/health/live", tags=["meta"])
    async def health_live() -> dict[str, str]:
        """Liveness: the process is up. Always 200 unless the process is dead.

        A failed liveness check tells an orchestrator to *restart* the container.
        """
        return {"status": "alive"}

    @app.get("/health/ready", tags=["meta"])
    async def health_ready() -> JSONResponse:
        """Readiness: dependencies reachable. 503 (not restart-worthy) if DB or Redis is down.

        A failed readiness check tells an orchestrator to stop *routing traffic* here without
        restarting — so a transient DB/Redis blip doesn't cause a restart storm.
        """
        checks = {"db": await _check_db(), "redis": await _check_redis()}
        ok = all(checks.values())
        return JSONResponse(
            status_code=200 if ok else 503,
            content={
                "status": "ready" if ok else "degraded",
                "checks": {name: ("ok" if up else "down") for name, up in checks.items()},
            },
        )

    # Routers registered in later phases:
    # app.include_router(search.router); app.include_router(skill.router); ...

    # Per-request trace context now; exporter wired in v2 (see module docstring).
    FastAPIInstrumentor.instrument_app(app)

    return app


app = create_app()
