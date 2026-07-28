from __future__ import annotations

from celery import Celery


celery_app = Celery(
    "myvidcraft_orchestrator",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/0",
    include=["workers.orchestrator.tasks"],
)

celery_app.conf.update(
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    worker_send_task_events=True,
)

# Celery CLI auto-discovers `app` by default.
app = celery_app

