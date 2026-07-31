"""
Anthropic client wrapper for the Phase 1 brain + script engines.

Design rule: **this module never raises into the pipeline.** Every failure mode
(no API key, refusal, rate limit, network error, schema mismatch) returns `None`,
and the caller falls back to the deterministic template generator. A run always
produces artifacts.

That fallback is also why no server-side `fallbacks` model is configured: for this
workload the template is a better last resort than a second model — it is free,
instant, and always schema-valid. Refusals are in any case unlikely here, since
the content domain is podcast scripting rather than the cyber/bio topics the
Claude Opus 5 classifiers target.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from apps.api.settings import settings

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_client: Any = None
_client_lock = threading.Lock()
_client_failed = False


def is_enabled() -> bool:
    """True when a real LLM call should be attempted."""
    if not settings.llm_enabled:
        return False
    if not _resolve_api_key():
        return False
    return not _client_failed


def _resolve_api_key() -> str:
    """
    Prefer the project-scoped MYVIDCRAFT_ANTHROPIC_API_KEY, then fall back to the
    SDK's own ANTHROPIC_API_KEY so an already-configured shell just works.
    """
    if settings.anthropic_api_key:
        return settings.anthropic_api_key
    import os

    return os.environ.get("ANTHROPIC_API_KEY", "")


def _get_client() -> Any:
    global _client, _client_failed
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:
            return _client
        try:
            import anthropic
        except ImportError:
            log.warning("anthropic SDK not installed; using offline template engines")
            _client_failed = True
            return None
        try:
            _client = anthropic.Anthropic(
                api_key=_resolve_api_key(),
                max_retries=settings.llm_max_retries,
                timeout=settings.llm_timeout_s,
            )
        except Exception as e:  # pragma: no cover - construction rarely fails
            log.warning("Anthropic client init failed (%s); using offline engines", e)
            _client_failed = True
            return None
    return _client


def generate_structured(
    *,
    system: str,
    user: str,
    output_format: type[T],
    max_tokens: int | None = None,
) -> T | None:
    """
    Ask Claude for a schema-validated object.

    Returns the parsed model, or `None` if the LLM path is unavailable or failed
    for any reason. Callers must treat `None` as "use the template".
    """
    if not is_enabled():
        return None

    client = _get_client()
    if client is None:
        return None

    try:
        import anthropic
    except ImportError:  # pragma: no cover - _get_client already guards this
        return None

    try:
        response = client.messages.parse(
            model=settings.llm_model,
            max_tokens=max_tokens or settings.llm_max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=output_format,
        )
    except anthropic.APIStatusError as e:
        # Covers 400/401/404/429/5xx. Non-retryable ones are already final here;
        # retryable ones have exhausted the SDK's own backoff.
        log.warning("LLM call failed (%s %s); falling back to template", e.status_code, e.message)
        return None
    except anthropic.APIConnectionError as e:
        log.warning("LLM connection error (%s); falling back to template", e)
        return None
    except ValidationError as e:
        log.warning("LLM output failed schema validation (%s); falling back to template", e)
        return None
    except Exception as e:
        log.warning("Unexpected LLM error (%s: %s); falling back to template", type(e).__name__, e)
        return None

    # Claude Opus 5 runs safety classifiers that can decline a request. That is a
    # normal HTTP 200 with an empty/partial body, so it must be checked before the
    # payload is read.
    if getattr(response, "stop_reason", None) == "refusal":
        category = getattr(getattr(response, "stop_details", None), "category", None)
        log.warning("LLM refused the request (category=%s); falling back to template", category)
        return None

    if getattr(response, "stop_reason", None) == "max_tokens":
        log.warning("LLM output hit max_tokens and is truncated; falling back to template")
        return None

    parsed = getattr(response, "parsed_output", None)
    if parsed is None:
        log.warning("LLM returned no parsed output; falling back to template")
        return None

    return parsed
