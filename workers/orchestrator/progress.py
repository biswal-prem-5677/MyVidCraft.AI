from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

import redis

from apps.api.settings import settings

log = logging.getLogger(__name__)

# Progress events are best-effort telemetry: a run must never stall or fail
# because the event bus is down. Two guards enforce that.
#
# 1. Explicit short timeouts. redis-py defaults to the OS TCP timeout, so a
#    *filtered* port (SYN dropped, as opposed to refused) blocks for tens of
#    seconds — multiplied by every event a run emits.
# 2. A circuit breaker. After a failure, publishing is skipped entirely for
#    RETRY_AFTER_S rather than re-paying the timeout on every subsequent event.
_CONNECT_TIMEOUT_S = 0.25
_SOCKET_TIMEOUT_S = 0.25
RETRY_AFTER_S = 30.0

_client_holder: redis.Redis | None = None
_lock = threading.Lock()
_disabled_until = 0.0


def _client() -> redis.Redis:
    global _client_holder
    if _client_holder is None:
        with _lock:
            if _client_holder is None:
                _client_holder = redis.from_url(
                    settings.redis_url,
                    decode_responses=True,
                    socket_connect_timeout=_CONNECT_TIMEOUT_S,
                    socket_timeout=_SOCKET_TIMEOUT_S,
                )
    return _client_holder


def _trip_breaker() -> None:
    global _disabled_until, _client_holder
    _disabled_until = time.monotonic() + RETRY_AFTER_S
    with _lock:
        _client_holder = None


def reset() -> None:
    """Clear the circuit breaker and cached client (used by tests)."""
    global _disabled_until, _client_holder
    with _lock:
        _client_holder = None
    _disabled_until = 0.0


def publish(run_id: str, event: dict[str, Any]) -> None:
    """Publish a run event. Never raises."""
    if time.monotonic() < _disabled_until:
        return

    payload = {
        "run_id": run_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        **event,
    }
    try:
        _client().publish(f"runs:{run_id}", json.dumps(payload, ensure_ascii=False))
    except Exception as e:
        log.debug("progress publish skipped (%s); muting for %ss", e, RETRY_AFTER_S)
        _trip_breaker()
