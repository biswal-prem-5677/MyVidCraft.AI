"""
Video renderer resolution.

Same shape as the speech-engine registry, and for the same reason: factories are
registered rather than instances, so importing this module does not import every
backend. That matters more here than it does for TTS — a MuseTalk registration
pulls in torch and model weights, which must not load on a machine that only
ever renders the pure-Python compositor.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from .base import VideoRenderer

DEFAULT_RENDERER = "slideshow"

_FACTORIES: dict[str, Callable[[], VideoRenderer]] = {}
_instances: dict[str, VideoRenderer] = {}
_lock = threading.Lock()


def register(name: str, factory: Callable[[], VideoRenderer]) -> None:
    """Register a renderer factory. Later registrations replace earlier ones."""
    _FACTORIES[name] = factory
    with _lock:
        _instances.pop(name, None)


def available_renderers() -> list[str]:
    return sorted(_FACTORIES)


def get_renderer(name: str | None = None) -> VideoRenderer:
    """
    Resolve a renderer by name, defaulting to the configured one.

    Raises `LookupError` on an unknown name, listing what is registered. A typo
    in configuration should fail loudly at the start of the video stage rather
    than silently rendering every episode with the wrong backend.
    """
    if name is None:
        from apps.api.settings import settings

        name = settings.video_engine

    if name not in _FACTORIES:
        raise LookupError(f"unknown video engine {name!r}; available: {', '.join(available_renderers())}")

    cached = _instances.get(name)
    if cached is not None:
        return cached

    with _lock:
        if (cached := _instances.get(name)) is not None:
            return cached
        renderer = _FACTORIES[name]()
        _instances[name] = renderer
    return renderer


def reset() -> None:
    """Drop cached instances (used by tests)."""
    with _lock:
        _instances.clear()


def _slideshow_factory() -> VideoRenderer:
    from .slideshow import SlideshowRenderer

    return SlideshowRenderer()


register(DEFAULT_RENDERER, _slideshow_factory)
