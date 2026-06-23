# Pragma backend

Run all commands from this directory (`backend/`).

```bash
pip install -e ".[dev]"                       # install deps + dev tools
uvicorn app.main:app --reload --port 8000     # API (hot path)
celery -A app.worker worker --loglevel=info -c 4   # ingestion workers (cold path)
celery -A app.worker beat --loglevel=info          # scheduled jobs
alembic upgrade head                          # apply migrations
alembic revision --autogenerate -m "msg"      # generate a migration (then review it)
pytest -v                                     # tests
ruff check app/ && ruff format app/           # lint + format
```

See [`../CLAUDE.md`](../CLAUDE.md) for architecture rules and [`../docs/BUILD_PLAN.md`](../docs/BUILD_PLAN.md) for the per-function plan.
