"""Celery application. Async tasks, webhooks and scheduled jobs are added in the rest of
M1 and M2 (ARCHITECTURE.md §4.2, RUNBOOK.md §8). This module already starts cleanly so
the `worker` and `beat` containers can be deployed."""

from __future__ import annotations

import os

from celery import Celery

celery_app = Celery(
    "lazula_ai",
    broker=os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"),
    include=[],
)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_time_limit=900,
    task_soft_time_limit=840,
    worker_prefetch_multiplier=1,
    task_default_queue="ai_fast",
    timezone="UTC",
)

app = celery_app  # `celery -A app.workers` looks for `app` or `celery`
