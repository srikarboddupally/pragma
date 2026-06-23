"""Celery application (cold path).

Tasks live in ``app.tasks.*`` and are added in later phases. The beat schedule below
references those not-yet-written tasks; uncomment each entry as its task lands.

Junior note: Celery tasks run synchronously. Our pipeline code is async, so each task
should enter the async world exactly once via ``asyncio.run(...)`` — don't scatter event
loops (see CLAUDE.md §4.9).
"""

from __future__ import annotations

from celery import Celery

from app.config import get_settings

_settings = get_settings()

celery_app = Celery(
    "pragma",
    broker=_settings.redis_url,
    backend=_settings.redis_url,
)

celery_app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,  # fair dispatch for long-running ingest tasks
    task_track_started=True,
)

# Discover tasks once the modules exist (Phase 2+).
# celery_app.autodiscover_tasks(["app.tasks"])

# Beat schedule (enable per task as it is implemented):
# celery_app.conf.beat_schedule = {
#     "sync-sources-every-15-min": {
#         "task": "app.tasks.ingest.sync_all_sources",
#         "schedule": 15 * 60,
#     },
#     "cluster-workspaces-hourly": {
#         "task": "app.tasks.skills.cluster_all_workspaces",
#         "schedule": 60 * 60,
#     },
# }
