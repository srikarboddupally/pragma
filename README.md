# Pragma

Company intelligence, executable. A knowledge layer + agent action layer for B2B SaaS support teams.

- **Spec / pitch:** [`PRAGMA.md`](PRAGMA.md)
- **Working guide (start here to contribute):** [`CLAUDE.md`](CLAUDE.md)
- **Per-function build plan:** [`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md)

## Layout

```
backend/    Python service (FastAPI + Celery + Postgres/pgvector)  — all of v1's value
frontend/   React + Vite dashboard (added in Phase 6)
docs/       build plan and design notes
```

## Quick start (local)

```bash
cp .env.example .env          # fill in keys as needed
docker compose up -d          # Postgres (pgvector) + Redis + api + worker + beat

cd backend
pip install -e ".[dev]"       # or: pip install -r requirements.txt
alembic upgrade head          # apply migrations (none yet — Phase 1)
pytest -v                     # run tests
ruff check app/ && ruff format app/
uvicorn app.main:app --reload --port 8000   # http://localhost:8000/health
```

## Status

Phase 0 (foundation) is the current scaffold. See the status board in [`CLAUDE.md`](CLAUDE.md#8-status-board).
