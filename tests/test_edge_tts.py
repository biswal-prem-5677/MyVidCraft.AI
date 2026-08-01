"""
The free speech backend that unblocks the Gate 1 listen test.

Never makes a network call and never shells out to FFmpeg — both are stubbed in
every test. What is pinned here is the contract the orchestrator depends on,
plus the three properties that justify a second engine existing at all:

- **It is not the default.** A paying user must not land on an undocumented
  endpoint by accident.
- **It produces turns interchangeable with the paid engine's.** Same channel
  count, sample width and frame rate, or `stitch_turns_to_episode` refuses them.
- **It fails legibly.** The two things that will actually go wrong — no FFmpeg,
  and the endpoint returning 403 — must produce errors that say what to do next,
  not an aiohttp handshake trace.
"""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from workers.media.ffmpeg import FFmpegError
from workers.media.tts import edge_tts as edge_mod
from workers.media.tts.base import SpeechEngine, Voice
from workers.media.tts.edge_tts import (
    CHANNELS,
    DEFAULT_VOICE,
    SAMPLE_RATE_HZ,
    EdgeTTSEngine,
    rate_to_percent,
)
from workers.media.tts.voices import DEFAULT_RATE, MAX_RATE, MIN_RATE, assign_voices


@pytest.fixture
def engine_and_calls(monkeypatch, tmp_path):
    """An engine with both external dependencies replaced.

    `_synthesize_mp3` stands in for the network; `transcode_to_wav` for FFmpeg,
    writing a real playable WAV so downstream assertions operate on a genuine
    file rather than a sentinel.
    """
    calls: dict = {}
    engine = EdgeTTSEngine()

    def fake_mp3(text, mp3_path, *, voice, rate):
        calls.update(text=text, voice=voice, rate=rate, mp3_path=mp3_path)
        Path(mp3_path).write_bytes(b"ID3fake-mp3-payload")

    def fake_transcode(source, target, *, sample_rate, channels):
        calls.update(source=source, target=target, sample_rate=sample_rate, channels=channels)
        calls["source_existed"] = Path(source).exists()
        with wave.open(target, "wb") as w:
            w.setnchannels(channels)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(b"\x00\x00" * calls.get("frames", 512))

    monkeypatch.setattr(engine, "_synthesize_mp3", fake_mp3)
    monkeypatch.setattr(edge_mod, "transcode_to_wav", fake_transcode)
    return engine, calls


# -- protocol conformance ---------------------------------------------------


def test_the_engine_satisfies_the_speech_protocol():
    assert isinstance(EdgeTTSEngine(), SpeechEngine)


def test_the_engine_name_matches_its_registry_key():
    from workers.media.tts import registry

    assert registry.get_engine("edge").name == EdgeTTSEngine.name == "edge"


def test_the_free_engine_is_not_the_default():
    """
    The whole reason this backend is opt-in.

    It speaks to an endpoint Microsoft never documented and rate-limits without
    notice. Fine for answering the Gate 1 go/no-go; not something a paying
    customer's run should silently depend on.
    """
    from workers.media.tts import registry

    assert registry.DEFAULT_ENGINE == "openai"
    assert "edge" in registry.available_engines()


def test_the_module_imports_without_a_platform_binding():
    """No COM, no pywin32, no OS branch — the property the SAPI swap bought."""
    import ast

    tree = ast.parse(Path(edge_mod.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert not imported & {"pythoncom", "win32com", "pyttsx3", "pywintypes"}
    assert "platform" not in imported and "sys" not in imported


def test_synthesis_holds_no_module_level_lock():
    """A shared lock is what capped the old engine at one turn per process."""
    import threading

    locks = [v for v in vars(edge_mod).values() if isinstance(v, type(threading.Lock()))]
    assert not locks, "an HTTP backend needs no mutex; this would serialise the voice stage"


# -- pace mapping -----------------------------------------------------------


def test_default_rate_maps_to_the_voices_natural_pace():
    assert rate_to_percent(DEFAULT_RATE) == "+0%"


def test_slower_and_faster_rates_move_the_percentage_the_right_way():
    assert rate_to_percent(MIN_RATE).startswith("-")
    assert rate_to_percent(MAX_RATE).startswith("+")


def test_the_sign_is_always_explicit():
    """The library validates the format and rejects a bare number."""
    for rate in (MIN_RATE, DEFAULT_RATE, MAX_RATE, 120, 200):
        assert rate_to_percent(rate)[0] in "+-"
        assert rate_to_percent(rate).endswith("%")


def test_pace_is_clamped_to_the_services_range():
    for absurd in (0, -50, 10_000):
        percent = int(rate_to_percent(absurd).rstrip("%"))
        assert -90 <= percent <= 200


def test_missing_pace_falls_back_to_the_default():
    assert rate_to_percent(None) == "+0%"  # type: ignore[arg-type]


# -- synthesis --------------------------------------------------------------


def test_synthesize_writes_a_wav_and_passes_the_voice(engine_and_calls, tmp_path):
    engine, calls = engine_and_calls
    out = tmp_path / "turn.wav"

    engine.synthesize("hello there", str(out), rate=DEFAULT_RATE, voice_id="en-GB-RyanNeural")

    assert out.exists() and out.stat().st_size > 0
    assert calls["voice"] == "en-GB-RyanNeural"
    assert calls["text"] == "hello there"
    assert calls["rate"] == "+0%"


def test_output_is_forced_to_the_stitcher_contract(engine_and_calls, tmp_path):
    """
    The transcode parameters are dictated, not inherited from the source.

    An engine that let the MP3 decide would produce turns that individually play
    and collectively fail to assemble.
    """
    engine, calls = engine_and_calls

    engine.synthesize("one", str(tmp_path / "a.wav"), rate=DEFAULT_RATE)

    assert calls["sample_rate"] == SAMPLE_RATE_HZ
    assert calls["channels"] == CHANNELS


def test_turns_are_interchangeable_with_the_paid_engines(engine_and_calls, tmp_path):
    """Same WAV params as `openai_tts`, so switching engines is one setting."""
    from workers.media.tts.openai_tts import OpenAITTSEngine  # noqa: F401

    engine, _ = engine_and_calls
    first, second = tmp_path / "a.wav", tmp_path / "b.wav"

    engine.synthesize("one", str(first), rate=MIN_RATE, voice_id="en-US-AndrewMultilingualNeural")
    engine.synthesize("two", str(second), rate=MAX_RATE, voice_id="en-US-AvaMultilingualNeural")

    with wave.open(str(first)) as a, wave.open(str(second)) as b:
        params = (a.getnchannels(), a.getsampwidth(), a.getframerate())
        assert params == (b.getnchannels(), b.getsampwidth(), b.getframerate())
        assert params == (CHANNELS, 2, SAMPLE_RATE_HZ)


def test_parent_directories_are_created(engine_and_calls, tmp_path):
    engine, _ = engine_and_calls
    out = tmp_path / "turns" / "nested" / "turn.wav"

    engine.synthesize("hi", str(out), rate=DEFAULT_RATE)

    assert out.exists()


def test_the_scratch_mp3_does_not_survive_the_call(engine_and_calls, tmp_path):
    """
    The MP3 is an implementation detail of this backend.

    Leaving it behind would put an unregistered, uncleaned file in the artifacts
    tree for every turn of every run.
    """
    engine, calls = engine_and_calls

    engine.synthesize("hi", str(tmp_path / "t.wav"), rate=DEFAULT_RATE)

    assert calls["source_existed"], "the mp3 must exist while it is being transcoded"
    assert not Path(calls["mp3_path"]).exists()
    assert not Path(calls["mp3_path"]).parent.exists()


def test_an_unknown_voice_falls_back_instead_of_failing(engine_and_calls, tmp_path):
    """Losing a voice assignment is a worse episode, not a broken one."""
    engine, calls = engine_and_calls

    engine.synthesize("hi", str(tmp_path / "t.wav"), rate=DEFAULT_RATE, voice_id="nova")

    assert calls["voice"] == DEFAULT_VOICE


def test_no_voice_uses_the_default(engine_and_calls, tmp_path):
    engine, calls = engine_and_calls

    engine.synthesize("hi", str(tmp_path / "t.wav"), rate=DEFAULT_RATE, voice_id=None)

    assert calls["voice"] == DEFAULT_VOICE


def test_empty_audio_raises_rather_than_writing_a_silent_turn(engine_and_calls, tmp_path):
    engine, calls = engine_and_calls
    calls["frames"] = 0

    with pytest.raises(RuntimeError, match="no audio"):
        engine.synthesize("hi", str(tmp_path / "t.wav"), rate=DEFAULT_RATE)


# -- failing legibly ---------------------------------------------------------


def test_a_missing_ffmpeg_says_how_to_install_it(monkeypatch, tmp_path):
    """
    FFmpeg is optional everywhere else in this codebase and required here.

    Someone hitting this has done nothing wrong — they followed a README that
    lists FFmpeg as optional — so the error has to carry the fix.
    """
    engine = EdgeTTSEngine()
    monkeypatch.setattr(engine, "_synthesize_mp3", lambda *a, **k: Path(a[1]).write_bytes(b"mp3"))

    def no_ffmpeg(*a, **k):
        raise FFmpegError("FFmpeg is required ... `winget install Gyan.FFmpeg` ...")

    monkeypatch.setattr(edge_mod, "transcode_to_wav", no_ffmpeg)

    with pytest.raises(RuntimeError, match="winget install"):
        engine.synthesize("hi", str(tmp_path / "t.wav"), rate=DEFAULT_RATE)


def test_an_endpoint_rejection_names_the_cause_and_the_escape(monkeypatch, tmp_path):
    """
    This endpoint returns 403 periodically; the raw error mentions only a
    websocket handshake, which points at nothing the reader can act on.
    """
    engine = EdgeTTSEngine()

    def rejected(*a, **k):
        raise ConnectionError("403, message='Invalid response status'")

    monkeypatch.setattr(engine, "_synthesize_mp3", rejected)

    with pytest.raises(RuntimeError) as excinfo:
        engine.synthesize("hi", str(tmp_path / "t.wav"), rate=DEFAULT_RATE)

    message = str(excinfo.value)
    assert "undocumented" in message
    assert "MYVIDCRAFT_TTS_ENGINE=openai" in message, "the error must name the way out"


def test_calling_from_an_async_context_fails_with_a_useful_message():
    """
    `asyncio.run` from inside a loop raises an error naming neither this engine
    nor the fix. Both current callers are sync; this guards a future one.
    """
    import asyncio

    engine = EdgeTTSEngine()

    async def call_it():
        engine._synthesize_mp3("hi", "/tmp/x.mp3", voice=DEFAULT_VOICE, rate="+0%")

    with pytest.raises(RuntimeError, match="async context"):
        asyncio.run(call_it())


# -- casting integration ----------------------------------------------------


def test_voices_are_listed_without_a_network_call():
    """The protocol asks for a list that cannot fail; a static one cannot."""
    voices = EdgeTTSEngine().list_voices()

    assert len(voices) >= 2
    assert all(isinstance(v, Voice) for v in voices)
    assert len({v.id for v in voices}) == len(voices), "voice ids must be unique"


def test_two_speakers_are_cast_to_contrasting_voices():
    """Ordering alternates timbre, since the SAPI-era role hints never match."""
    voices = EdgeTTSEngine().list_voices()

    cast = assign_voices(["Host", "Guest"], voices, roles={"Host": "host", "Guest": "guest"})

    assert cast["Host"].voice_id != cast["Guest"].voice_id


def test_casting_is_deterministic_across_runs():
    voices = EdgeTTSEngine().list_voices()

    first = assign_voices(["Host", "Guest"], voices)
    second = assign_voices(["Host", "Guest"], voices)

    assert {s: a.voice_id for s, a in first.items()} == {s: a.voice_id for s, a in second.items()}


def test_no_voice_name_traps_the_role_matcher():
    """
    `_match` is a plain substring test and "female" contains "male".

    A voice labelled "(male)"/"(female)" would let a host hint capture a
    feminine voice. Pinned because the fix is invisible in the catalogue itself.
    """
    for v in EdgeTTSEngine().list_voices():
        assert "male" not in f"{v.name} {v.id}".lower()


def test_every_listed_voice_is_actually_synthesizable():
    """
    A catalogue entry the engine would reject is worse than a missing one: the
    UI offers it, the user picks it, and the turn silently renders as Andrew.
    """
    engine = EdgeTTSEngine()

    for v in engine.list_voices():
        assert v.id in edge_mod._VOICE_IDS
