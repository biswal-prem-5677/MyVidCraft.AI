from __future__ import annotations

import json
from typing import AsyncIterator

import redis.asyncio as redis

from .settings import settings

# Match the publisher's timeouts: without these, a filtered (rather than refused)
# Redis port blocks for the OS TCP timeout before the websocket can fall back to
# database polling.
_CONNECT_TIMEOUT_S = 0.5
_SOCKET_TIMEOUT_S = 0.5


def run_channel(run_id: str) -> str:
    return f"runs:{run_id}"


async def subscribe_run_events(run_id: str) -> AsyncIterator[dict]:
    r = redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=_CONNECT_TIMEOUT_S,
        socket_timeout=_SOCKET_TIMEOUT_S,
    )
    pubsub = r.pubsub()
    await pubsub.subscribe(run_channel(run_id))
    try:
        async for msg in pubsub.listen():
            if msg is None or msg.get("type") != "message":
                continue
            data = msg.get("data")
            if not data:
                continue
            try:
                yield json.loads(data)
            except json.JSONDecodeError:
                yield {"type": "log", "message": str(data)}
    finally:
        try:
            await pubsub.unsubscribe(run_channel(run_id))
        finally:
            await pubsub.aclose()
            await r.aclose()
