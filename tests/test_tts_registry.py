"""Speech engine resolution.

Never instantiates the real system engine — every test registers a fake, which
is itself the point of the seam.
"""

from __future__ import annotations

import pytest

from workers.media.tts import registry
from workers.media.tts.base import SpeechEngine, Voice


class FakeEngine:
    name = "fake"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int, str | None]] = []

    def list_voices(self) -> list[Voice]:
        return [Voice(id="fake-a", name="Fake A")]

    def synthesize(self, text: str, out_path: str, *, rate: int, voice_id: str | None = None) -> None:
        self.calls.append((text, out_path, rate, voice_id))


@pytest.fixture(autouse=True)
def _clean_registry():
    yield
    registry.reset()


def test_a_fake_engine_satisfies_the_protocol():
    assert isinstance(FakeEngine(), SpeechEngine)


def test_registered_engine_resolves_by_name():
    registry.register("fake", FakeEngine)

    assert registry.get_engine("fake").name == "fake"
    assert "fake" in registry.available_engines()


def test_instances_are_cached():
    """Engines own process-wide resources, so one instance per name per process."""
    registry.register("fake", FakeEngine)

    assert registry.get_engine("fake") is registry.get_engine("fake")


def test_re_registering_replaces_the_cached_instance():
    registry.register("fake", FakeEngine)
    first = registry.get_engine("fake")
    registry.register("fake", FakeEngine)

    assert registry.get_engine("fake") is not first


def test_unknown_engine_lists_the_valid_names():
    with pytest.raises(LookupError, match="unknown TTS engine"):
        registry.get_engine("does-not-exist")


def test_default_engine_is_registered():
    """The pyttsx3 factory is registered at import, without being instantiated."""
    assert registry.DEFAULT_ENGINE in registry.available_engines()


def test_settings_select_the_engine(monkeypatch):
    from apps.api.settings import settings

    registry.register("fake", FakeEngine)
    monkeypatch.setattr(settings, "tts_engine", "fake")

    assert registry.get_engine().name == "fake"
