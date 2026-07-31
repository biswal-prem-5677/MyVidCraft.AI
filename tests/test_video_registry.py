"""
Video renderer resolution.

Mirrors `test_tts_registry.py`, because the seam is deliberately the same shape.
The reason instances are cached and factories are registered rather than
instances is that a neural backend pulls in torch and model weights — resolving a
name must not import a backend the machine never uses.
"""

from __future__ import annotations

import pytest

from workers.media.video import registry
from workers.media.video.base import EpisodeVideoSpec, RenderResult, VideoRenderer


class FakeRenderer:
    name = "fake-video"

    def __init__(self) -> None:
        self.rendered: list[EpisodeVideoSpec] = []

    def available(self) -> tuple[bool, str]:
        return True, "ok"

    def render(self, spec: EpisodeVideoSpec) -> RenderResult:
        self.rendered.append(spec)
        return RenderResult(video_path="episode.mp4")


@pytest.fixture(autouse=True)
def _clean_registry():
    yield
    registry.reset()


def test_a_fake_renderer_satisfies_the_protocol():
    assert isinstance(FakeRenderer(), VideoRenderer)


def test_registered_renderer_resolves_by_name():
    registry.register("fake-video", FakeRenderer)

    assert registry.get_renderer("fake-video").name == "fake-video"
    assert "fake-video" in registry.available_renderers()


def test_instances_are_cached():
    registry.register("fake-video", FakeRenderer)

    assert registry.get_renderer("fake-video") is registry.get_renderer("fake-video")


def test_re_registering_replaces_the_cached_instance():
    registry.register("fake-video", FakeRenderer)
    first = registry.get_renderer("fake-video")
    registry.register("fake-video", FakeRenderer)

    assert registry.get_renderer("fake-video") is not first


def test_unknown_renderer_lists_the_valid_names():
    """A configuration typo must fail loudly, not silently pick a default."""
    with pytest.raises(LookupError, match="unknown video engine"):
        registry.get_renderer("does-not-exist")

    with pytest.raises(LookupError, match=registry.DEFAULT_RENDERER):
        registry.get_renderer("does-not-exist")


def test_the_default_renderer_is_registered_without_being_instantiated():
    assert registry.DEFAULT_RENDERER in registry.available_renderers()


def test_settings_select_the_renderer(monkeypatch):
    from apps.api.settings import settings

    registry.register("fake-video", FakeRenderer)
    monkeypatch.setattr(settings, "video_engine", "fake-video")

    assert registry.get_renderer().name == "fake-video"


def test_the_shipped_renderer_reports_its_own_readiness():
    """`available()` is what lets a missing FFmpeg degrade instead of failing."""
    ok, detail = registry.get_renderer(registry.DEFAULT_RENDERER).available()

    assert isinstance(ok, bool)
    assert detail
    if not ok:
        assert "ffmpeg" in detail.lower()
