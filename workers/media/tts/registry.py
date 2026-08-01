"""
Speech engine resolution.

Factories are registered rather than instances, so importing this module does not
import every backend. That matters as soon as a second engine arrives: a GPU
model with heavy imports must not be loaded on a machine that only ever uses the
system voice.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from .base import SpeechEngine

log = logging.getLogger(__name__)

DEFAULT_ENGINE = "openai"

_FACTORIES: dict[str, Callable[[], SpeechEngine]] = {}
_instances: dict[str, SpeechEngine] = {}
_lock = threading.Lock()


def register(name: str, factory: Callable[[], SpeechEngine]) -> None:
    """Register an engine factory. Later registrations replace earlier ones."""
    _FACTORIES[name] = factory
    with _lock:
        _instances.pop(name, None)


def available_engines() -> list[str]:
    return sorted(_FACTORIES)


def get_engine(name: str | None = None) -> SpeechEngine:
    """
    Resolve an engine by name, defaulting to the configured one.

    Instances are cached: engines own expensive process-wide resources (the
    system speech engine is a single shared COM object, and a neural backend
    holds model weights), so one per name per process is the correct lifetime.

    Raises `LookupError` on an unknown name — a typo in configuration should fail
    at the first synthesis attempt with a list of valid names, not silently
    render every episode in the wrong voice.
    """
    if name is None:
        from apps.api.settings import settings

        name = settings.tts_engine

    if name not in _FACTORIES:
        raise LookupError(f"unknown TTS engine {name!r}; available: {', '.join(available_engines())}")

    cached = _instances.get(name)
    if cached is not None:
        return cached

    with _lock:
        if (cached := _instances.get(name)) is not None:
            return cached
        engine = _FACTORIES[name]()
        _instances[name] = engine
    return engine


def reset() -> None:
    """Drop cached instances (used by tests)."""
    with _lock:
        _instances.clear()


def _openai_factory() -> SpeechEngine:
    from .openai_tts import OpenAITTSEngine

    return OpenAITTSEngine()


def _edge_factory() -> SpeechEngine:
    from .edge_tts import EdgeTTSEngine

    return EdgeTTSEngine()


register(DEFAULT_ENGINE, _openai_factory)

# Free, keyless, and not the default. It renders the same neural voices Edge
# uses for read-aloud, which makes it the right engine for answering Gate 1's
# "would you publish this?" without a funded account — but it speaks to an
# undocumented endpoint that rate-limits without notice, so it is opt-in via
# MYVIDCRAFT_TTS_ENGINE=edge rather than something a paying user could land on.
register("edge", _edge_factory)
