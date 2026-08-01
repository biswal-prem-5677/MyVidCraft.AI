"""
Free cloud TTS over the Microsoft Edge read-aloud service.

Why this exists alongside the paid backend
------------------------------------------
`openai_tts.py` is the engine this product ships on. This one exists so the Gate
1 go/no-go — *generate an episode, listen to it, would you publish it?* — can be
answered without a funded API account. It needs no key, no card and no signup,
and it renders the same neural voices Edge uses for read-aloud, which are at
least the equal of the paid default. That makes it a fair test: judging quality
here and then paying for OpenAI later moves the answer up, never down.

Not the shipping default, deliberately
--------------------------------------
The endpoint is undocumented. Microsoft never published it, periodically
tightens its handshake — the library has shipped several releases purely to
chase 403s — and rate-limits by an undisclosed amount. That is an acceptable
dependency for verifying a product and an unacceptable one for billing
customers. `registry.DEFAULT_ENGINE` therefore stays `openai`; this backend is
opt-in via `MYVIDCRAFT_TTS_ENGINE=edge`, and moving between them is one setting.

The FFmpeg dependency, and why it is required here
--------------------------------------------------
The service emits MP3 only — the output format is hardcoded in the library, so
there is no WAV to ask for. `stitch_turns_to_episode` needs WAV, and needs every
turn in a run to agree on channel count, sample width and frame rate, so each
turn is transcoded to a fixed 24 kHz mono 16-bit PCM. That matches what the
OpenAI backend returns natively, so the two engines produce interchangeable
turns.

FFmpeg is optional everywhere else in this codebase. It is not optional here,
and `transcode_to_wav` raises rather than degrading: a missing MP3 costs an
artifact, but a missing turn costs a hole in the episode.

Module naming
-------------
`edge_tts.py` mirrors `openai_tts.py`, matching the rule in ARCHITECTURE.md that
modules are named after the engine that owns them. Python 3 resolves `import
edge_tts` inside this file to the top-level package, not to itself — absolute
imports are the default — so the name collision is safe.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

from ..ffmpeg import FFmpegError, transcode_to_wav
from .base import Voice
from .voices import DEFAULT_RATE, MAX_RATE, MIN_RATE

log = logging.getLogger(__name__)

#: WAV parameters every turn is normalised to. Chosen to match what the OpenAI
#: backend returns so the stitcher sees one contract regardless of engine.
SAMPLE_RATE_HZ = 24000
CHANNELS = 1

#: A curated cast, not the full catalogue.
#:
#: The service exposes 322 voices, 47 of them English. Putting all of those in
#: the UI's dropdown would be worse than useless, so this is the shortlist worth
#: casting from: the four "Conversation" voices Microsoft ships for Copilot,
#: which are the newest and clearly the best for dialogue, followed by two
#: British and two broadcast-styled alternatives. Add a `ShortName` from
#: `edge-tts --list-voices` here to widen it.
#:
#: Order is load-bearing, exactly as in `openai_tts.py`. `assign_voices` falls
#: back to round-robin over this list when its role hints do not match — and
#: they do not, being SAPI-era ("david", "zira") — so alternating timbre here is
#: what stops a two-speaker episode sounding like one person.
#:
#: Deliberately NOT labelled "(male)"/"(female)": the role matcher is a plain
#: substring test and "female" contains "male", so a gendered suffix would let a
#: host hint capture a feminine voice.
_VOICES: tuple[tuple[str, str], ...] = (
    ("en-US-AndrewMultilingualNeural", "Andrew — warm, confident"),
    ("en-US-AvaMultilingualNeural", "Ava — expressive, friendly"),
    ("en-US-BrianMultilingualNeural", "Brian — approachable, casual"),
    ("en-US-EmmaMultilingualNeural", "Emma — cheerful, clear"),
    ("en-GB-RyanNeural", "Ryan — British, even"),
    ("en-GB-SoniaNeural", "Sonia — British, bright"),
    ("en-US-ChristopherNeural", "Christopher — reliable, authoritative"),
    ("en-US-AriaNeural", "Aria — positive, broadcast"),
)

_VOICE_IDS = frozenset(v for v, _ in _VOICES)

DEFAULT_VOICE = "en-US-AndrewMultilingualNeural"

# The service's own bounds on the rate adjustment. `pace_to_rate` already clamps
# wpm to 90..220, so the mapped percentage lands in -49..+26 and never
# approaches these; they are here so widening the wpm range degrades rather than
# producing a request the service rejects.
_MIN_PERCENT = -90
_MAX_PERCENT = 200


def rate_to_percent(rate: int) -> str:
    """
    Map an engine rate (words per minute) onto the service's rate adjustment.

    The API takes a signed percentage string relative to the voice's natural
    pace, so `DEFAULT_RATE` maps to `+0%` and the dialogue schema's `pace_wpm`
    scales linearly either side of it. The sign is always explicit — the library
    validates the format and rejects a bare number.
    """
    rate = max(MIN_RATE, min(MAX_RATE, int(rate or DEFAULT_RATE)))
    percent = round((rate / DEFAULT_RATE - 1) * 100)
    percent = max(_MIN_PERCENT, min(_MAX_PERCENT, percent))
    return f"{percent:+d}%"


def _assert_audible(target: Path) -> None:
    """
    Reject a turn that is a WAV header and nothing else.

    File size is not the test: an empty WAV is still 44 bytes, so a size check
    passes it through and the failure surfaces much later as an episode with a
    silent gap where a turn should be. Reading the frame count instead also
    proves the file parses, which is what the stitcher will require of it.
    """
    import wave

    if not target.exists():
        raise RuntimeError(f"TTS wrote no file for {target.name}")
    try:
        with wave.open(str(target), "rb") as w:
            frames = w.getnframes()
    except (OSError, wave.Error) as e:
        raise RuntimeError(f"TTS produced an unreadable WAV for {target.name}: {e}") from e
    if frames == 0:
        raise RuntimeError(f"TTS returned no audio for {target.name}")


class EdgeTTSEngine:
    """`SpeechEngine` over the Edge read-aloud service.

    Stateless. Like the OpenAI backend and unlike the SAPI one both replaced,
    nothing here is a shared exclusive resource, so concurrent synthesis is safe
    and is the intended usage.
    """

    name = "edge"

    def list_voices(self) -> list[Voice]:
        """
        The curated cast above, without a network call.

        The service does publish a catalogue endpoint, but calling it here would
        put a network round-trip on every page load of the UI and give the
        dropdown 322 entries. The protocol also asks for `[]` rather than an
        exception when a backend is unusable, and a static list cannot fail.
        """
        return [Voice(id=vid, name=label) for vid, label in _VOICES]

    def _synthesize_mp3(self, text: str, mp3_path: str, *, voice: str, rate: str) -> None:
        """
        Render to MP3 via the library's async API.

        `asyncio.run` is safe in both callers: the Celery worker synthesises on a
        plain thread, and FastAPI runs the sync `/voices` handler in a
        threadpool. Neither has a running loop. The guard below turns the
        confusing "asyncio.run() cannot be called from a running event loop" into
        something that names this engine, in case a future caller is async.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass  # No loop on this thread, which is what we want.
        else:
            raise RuntimeError(
                "EdgeTTSEngine.synthesize was called from an async context; "
                "run it in a worker thread (asyncio.to_thread) instead"
            )

        import edge_tts

        async def _run() -> None:
            communicate = edge_tts.Communicate(text, voice, rate=rate)
            await communicate.save(mp3_path)

        asyncio.run(_run())

    def synthesize(self, text: str, out_path: str, *, rate: int = DEFAULT_RATE, voice_id: str | None = None) -> None:
        try:
            import edge_tts  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "the `edge-tts` package is not installed but MYVIDCRAFT_TTS_ENGINE=edge; "
                "run `pip install -r requirements.txt`"
            ) from e

        # An unknown id must not fail the turn — losing one voice assignment is a
        # worse episode, not a broken one.
        voice = voice_id if voice_id in _VOICE_IDS else DEFAULT_VOICE
        if voice_id and voice_id not in _VOICE_IDS:
            log.warning("unknown voice %r; falling back to %s", voice_id, DEFAULT_VOICE)

        target = Path(out_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        # The MP3 is scratch: it exists only to be transcoded, and leaving it
        # next to the WAV would put a file in the artifacts tree that nothing
        # registers and nothing cleans up.
        with tempfile.TemporaryDirectory(prefix="edge-tts-") as scratch:
            mp3_path = str(Path(scratch) / "turn.mp3")
            try:
                self._synthesize_mp3(text, mp3_path, voice=voice, rate=rate_to_percent(rate))
            except Exception as e:
                # The library raises aiohttp errors for the 403s this endpoint
                # periodically returns. Naming the cause matters here: the error
                # otherwise mentions only a websocket handshake, which points at
                # nothing the reader can act on.
                raise RuntimeError(
                    f"Edge TTS failed to render a turn ({type(e).__name__}: {e}). "
                    "This endpoint is undocumented and rate-limits without notice; "
                    "try `pip install --upgrade edge-tts`, wait, or set "
                    "MYVIDCRAFT_TTS_ENGINE=openai with a paid key."
                ) from e

            try:
                transcode_to_wav(mp3_path, out_path, sample_rate=SAMPLE_RATE_HZ, channels=CHANNELS)
            except FFmpegError as e:
                raise RuntimeError(f"Edge TTS returned audio but it could not be converted to WAV: {e}") from e

        _assert_audible(target)
