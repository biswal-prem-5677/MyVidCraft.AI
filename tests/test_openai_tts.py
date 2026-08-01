"""
The cloud speech backend that replaced SAPI.

Never makes a network call — the client is stubbed in every test. What is pinned
here is the contract the orchestrator depends on, plus the two properties that
were the entire reason for the swap:

- **No platform binding.** The module must import and answer on any OS. The
  engine it replaced needed Windows COM.
- **No shared exclusive resource.** Concurrent synthesis must be safe. The engine
  it replaced serialised every call behind a process-wide lock, which capped a
  whole worker at one turn at a time.
"""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from workers.media.tts import openai_tts
from workers.media.tts.base import SpeechEngine, Voice
from workers.media.tts.openai_tts import DEFAULT_VOICE, OpenAITTSEngine, rate_to_speed
from workers.media.tts.voices import DEFAULT_RATE, MAX_RATE, MIN_RATE, assign_voices


class StubResponse:
    """Stands in for the SDK's audio response; writes a real, playable WAV."""

    def __init__(self, recorder: dict, *, frames: int = 512) -> None:
        self._recorder = recorder
        self._frames = frames

    def write_to_file(self, path: str) -> None:
        self._recorder["written_to"] = path
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(b"\x00\x00" * self._frames)


class StubClient:
    def __init__(self, recorder: dict, *, frames: int = 512) -> None:
        self._recorder = recorder
        self._frames = frames
        self.audio = self

    @property
    def speech(self):
        return self

    def create(self, **kwargs):
        self._recorder.update(kwargs)
        return StubResponse(self._recorder, frames=self._frames)


@pytest.fixture
def engine_and_calls(monkeypatch):
    calls: dict = {}
    engine = OpenAITTSEngine()
    monkeypatch.setattr(engine, "_get_client", lambda: StubClient(calls))
    return engine, calls


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setattr(openai_tts, "_resolve_api_key", lambda: "sk-test")


# -- protocol conformance ---------------------------------------------------


def test_the_engine_satisfies_the_speech_protocol():
    assert isinstance(OpenAITTSEngine(), SpeechEngine)


def test_the_engine_name_matches_its_registry_key():
    from workers.media.tts import registry

    assert OpenAITTSEngine.name == registry.DEFAULT_ENGINE


def test_the_module_imports_without_a_platform_binding():
    """
    The point of the swap: no COM, no pywin32, no OS branch anywhere.

    Checks the parsed import graph rather than the file text — the module
    docstring names the old stack to explain why it went, and a substring scan
    would flag that prose as a regression.
    """
    import ast

    tree = ast.parse(Path(openai_tts.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert not imported & {"pythoncom", "win32com", "pyttsx3", "pywintypes"}
    assert "platform" not in imported and "sys" not in imported


def test_synthesis_holds_no_module_level_lock():
    """
    A shared lock is what capped the old engine at one turn per process.

    Guards against someone adding one back for a backend that does not need it.
    """
    import threading

    locks = [v for v in vars(openai_tts).values() if isinstance(v, type(threading.Lock()))]
    assert not locks, "an HTTP backend needs no mutex; this would serialise the voice stage"


# -- pace mapping -----------------------------------------------------------


def test_default_rate_maps_to_natural_speed():
    assert rate_to_speed(DEFAULT_RATE) == 1.0


def test_slower_and_faster_rates_move_speed_the_right_way():
    assert rate_to_speed(MIN_RATE) < 1.0
    assert rate_to_speed(MAX_RATE) > 1.0


def test_pace_is_clamped_to_the_api_range():
    """A miscounted pace must not produce a speed the API rejects."""
    for absurd in (0, -50, 10_000):
        assert 0.25 <= rate_to_speed(absurd) <= 4.0


def test_missing_pace_falls_back_to_the_default():
    assert rate_to_speed(None) == 1.0  # type: ignore[arg-type]


# -- synthesis --------------------------------------------------------------


def test_synthesize_writes_a_wav_and_passes_the_voice(engine_and_calls, tmp_path):
    engine, calls = engine_and_calls
    out = tmp_path / "turn.wav"

    engine.synthesize("hello there", str(out), rate=DEFAULT_RATE, voice_id="nova")

    assert out.exists() and out.stat().st_size > 0
    assert calls["voice"] == "nova"
    assert calls["input"] == "hello there"
    assert calls["response_format"] == "wav"


def test_output_matches_the_stitcher_contract(engine_and_calls, tmp_path):
    """`stitch_turns_to_episode` refuses turns whose WAV params disagree."""
    engine, _ = engine_and_calls
    first, second = tmp_path / "a.wav", tmp_path / "b.wav"

    engine.synthesize("one", str(first), rate=MIN_RATE, voice_id="onyx")
    engine.synthesize("two", str(second), rate=MAX_RATE, voice_id="nova")

    with wave.open(str(first)) as a, wave.open(str(second)) as b:
        assert (a.getnchannels(), a.getsampwidth(), a.getframerate()) == (
            b.getnchannels(),
            b.getsampwidth(),
            b.getframerate(),
        )


def test_parent_directories_are_created(engine_and_calls, tmp_path):
    engine, _ = engine_and_calls
    out = tmp_path / "turns" / "nested" / "turn.wav"

    engine.synthesize("hi", str(out), rate=DEFAULT_RATE)

    assert out.exists()


def test_an_unknown_voice_falls_back_instead_of_failing(engine_and_calls, tmp_path):
    """Losing a voice assignment is a worse episode, not a broken one."""
    engine, calls = engine_and_calls

    engine.synthesize("hi", str(tmp_path / "t.wav"), rate=DEFAULT_RATE, voice_id="not-a-voice")

    assert calls["voice"] == DEFAULT_VOICE


def test_no_voice_uses_the_default(engine_and_calls, tmp_path):
    engine, calls = engine_and_calls

    engine.synthesize("hi", str(tmp_path / "t.wav"), rate=DEFAULT_RATE, voice_id=None)

    assert calls["voice"] == DEFAULT_VOICE


def test_empty_audio_raises_rather_than_writing_a_silent_turn(monkeypatch, tmp_path):
    engine = OpenAITTSEngine()
    calls: dict = {}
    monkeypatch.setattr(engine, "_get_client", lambda: StubClient(calls, frames=0))

    with pytest.raises(RuntimeError, match="no audio"):
        engine.synthesize("hi", str(tmp_path / "t.wav"), rate=DEFAULT_RATE)


# -- configuration degradation ----------------------------------------------


def test_without_a_key_voices_are_empty_not_an_exception(monkeypatch):
    """The protocol asks for [] so casting degrades to the engine default."""
    monkeypatch.setattr(openai_tts, "_resolve_api_key", lambda: "")

    assert OpenAITTSEngine().list_voices() == []


def test_without_a_key_synthesis_fails_legibly(monkeypatch, tmp_path):
    monkeypatch.setattr(openai_tts, "_resolve_api_key", lambda: "")

    with pytest.raises(RuntimeError, match="MYVIDCRAFT_OPENAI_API_KEY"):
        OpenAITTSEngine().synthesize("hi", str(tmp_path / "t.wav"), rate=DEFAULT_RATE)


def test_voices_are_listed_when_configured(with_key):
    voices = OpenAITTSEngine().list_voices()

    assert len(voices) >= 2
    assert all(isinstance(v, Voice) for v in voices)
    assert len({v.id for v in voices}) == len(voices), "voice ids must be unique"


# -- casting integration ----------------------------------------------------


def test_two_speakers_are_cast_to_contrasting_voices(with_key):
    """
    The catalogue is ordered so round-robin alternates timbre.

    These voice names do not match the role hints (which are SAPI-era: "david",
    "zira"), so casting falls through to round-robin — and the ordering is what
    stops a two-speaker episode sounding like one person.
    """
    voices = OpenAITTSEngine().list_voices()

    cast = assign_voices(["Host", "Guest"], voices, roles={"Host": "host", "Guest": "guest"})

    assert cast["Host"].voice_id != cast["Guest"].voice_id


def test_casting_is_deterministic_across_runs(with_key):
    voices = OpenAITTSEngine().list_voices()

    first = assign_voices(["Host", "Guest"], voices)
    second = assign_voices(["Host", "Guest"], voices)

    assert {s: a.voice_id for s, a in first.items()} == {s: a.voice_id for s, a in second.items()}


def test_no_voice_name_traps_the_role_matcher(with_key):
    """
    `_match` is a plain substring test and "female" contains "male".

    A voice labelled "(male)"/"(female)" would let a host hint capture a
    feminine voice. Pinned because the fix is invisible in the catalogue itself.
    """
    for v in OpenAITTSEngine().list_voices():
        assert "male" not in f"{v.name} {v.id}".lower()
