"""FastAPI application factory (hot path).

Phase 0 registers only a health check. Feature routers (search, skill, ask, actions,
approvals, audit, sources) are added in later phases. Remember the hard rule: nothing
mounted here may trigger cold-path work (Celery, embedding new docs, extraction).
"""

from __future__ import annotations

from fastapi import FastAPI

from app.logging import configure_logging


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="Pragma API", version="0.1.0")

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # Routers registered in later phases:
    # app.include_router(search.router); app.include_router(skill.router); ...

    return app


app = create_app()
