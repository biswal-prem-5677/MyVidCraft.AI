from __future__ import annotations

from celery import Celery

from apps.api.settings import settings

celery_app = Celery(
    "myvidcraft_orchestrator",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["workers.orchestrator.tasks"],
)

celery_app.conf.update(
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    worker_send_task_events=True,
    # Fail fast instead of retrying the broker connection. The API probes for a
    # reachable broker before enqueueing and falls back to in-process execution,
    # so a long retry loop here would just block the HTTP request that starts a run.
    broker_connection_retry_on_startup=False,
    broker_transport_options={
        "max_retries": 0,
        "socket_connect_timeout": 0.25,
        "socket_timeout": 0.25,
    },
)

# Celery CLI auto-discovers `app` by default.
app = celery_app
