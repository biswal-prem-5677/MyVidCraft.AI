from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import redis

from apps.api.settings import settings


def _client() -> redis.Redis:
    return redis.from_url(settings.redis_url, decode_responses=True)


def publish(run_id: str, event: dict[str, Any]) -> None:
    payload = {
        "run_id": run_id,
        "ts": datetime.utcnow().isoformat() + "Z",
        **event,
    }
    try:
        _client().publish(f"runs:{run_id}", json.dumps(payload, ensure_ascii=False))
    except Exception:
        # Dev fallback: allow Phase 1 to run without Redis.
        return

