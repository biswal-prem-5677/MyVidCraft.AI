"""
Cloud TTS over the OpenAI speech API.

Why this replaced the system engine
-----------------------------------
The previous default (SAPI via pyttsx3) could not be deployed. It needed Windows
COM, so it did not run in a Linux container at all, and it held a process-wide
lock around synthesis, so one worker rendered one turn at a time no matter how
many cores it had. Those two facts together capped the product at "runs on the
author's laptop, for one user".

This backend is an HTTP call. It has no platform binding and no shared mutable
resource, so N turns can be in flight at once and the container is a plain
`python:slim`. That is the entire reason for the swap; the better voice quality
is a bonus that happens to also be the product's weakest axis.

Retry lives in the SDK
----------------------
`max_retries` is set on the client rather than leaning on the orchestrator's
task-level retry. A transient blip should re-send *one turn*, not re-run the
whole voice stage — and `RETRYABLE_ERRORS` in `tasks.py` is scoped to OSError,
which the SDK's connection errors are not. Getting this wrong would mean either
no retry at all or re-synthesising every turn to recover one.

Contract notes
--------------
`response_format="wav"` returns 24 kHz mono 16-bit PCM on every call, which is
what `stitch_turns_to_episode` requires: it refuses to concatenate turns whose
channel count, sample width or frame rate disagree.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .base import Voice
from .voices import DEFAULT_RATE, MAX_RATE, MIN_RATE

log = logging.getLogger(__name__)

#: The six voices available on every speech model OpenAI currently serves.
#:
#: Order is load-bearing. `assign_voices` falls back to round-robin over this
#: list when an engine's voice names do not match its role hints, and these do
#: not — the hints are SAPI-era ("david", "zira"). Alternating masculine and
#: feminine here means the default two-speaker cast comes out as contrasting
#: voices without any name matching at all.
#:
#: Deliberately NOT named "Onyx (male)" to help the hints match: the role
#: matcher is a plain substring test, and "female" contains "male", so a
#: gendered suffix would let a host hint capture a feminine voice.
_VOICES: tuple[tuple[str, str], ...] = (
    ("onyx", "Onyx — deep, measured"),
    ("nova", "Nova — warm, bright"),
    ("echo", "Echo — even, neutral"),
    ("shimmer", "Shimmer — light, airy"),
    ("fable", "Fable — expressive, British"),
    ("alloy", "Alloy — flat, neutral"),
)

_VOICE_IDS = frozenset(v for v, _ in _VOICES)

DEFAULT_VOICE = "onyx"

# The API's own bounds. `pace_to_rate` already clamps wpm to 90..220, so the
# mapped speed lands in 0.51..1.26 and never approaches these — they are here so
# a future widening of the wpm range degrades instead of erroring.
_MIN_SPEED = 0.25
_MAX_SPEED = 4.0


def rate_to_speed(rate: int) -> float:
    """
    Map an engine rate (words per minute) onto the API's speed multiplier.

    `DEFAULT_RATE` is the natural speaking pace of these voices, so it maps to
    1.0 and the schema's `pace_wpm` scales linearly either side of it.
    """
    rate = max(MIN_RATE, min(MAX_RATE, int(rate or DEFAULT_RATE)))
    return max(_MIN_SPEED, min(_MAX_SPEED, round(rate / DEFAULT_RATE, 3)))


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


def _resolve_api_key() -> str:
    """Project-scoped key first, then the SDK's own, matching `llm.py`."""
    import os

    from apps.api.settings import settings

    return settings.openai_api_key or os.environ.get("OPENAI_API_KEY", "")


class OpenAITTSEngine:
    """`SpeechEngine` over the OpenAI speech API.

    Stateless apart from a cached HTTP client. Unlike the system engine this
    replaced, nothing here is a shared exclusive resource, so concurrent calls
    are safe and are the intended usage.
    """

    name = "openai"

    def __init__(self) -> None:
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client

        key = _resolve_api_key()
        if not key:
            return None

        try:
            from openai import OpenAI
        except ImportError:
            log.error("openai SDK is not installed; no speech backend is available")
            return None

        from apps.api.settings import settings

        self._client = OpenAI(
            api_key=key,
            max_retries=settings.tts_max_retries,
            timeout=settings.tts_timeout_s,
        )
        return self._client

    def list_voices(self) -> list[Voice]:
        """
        The fixed catalogue this API serves.

        Returned without a network call — the set is static, and the protocol
        asks for `[]` rather than an exception when the backend is unusable, so
        casting degrades to the engine default instead of failing the run.
        """
        if not _resolve_api_key():
            log.warning("no OpenAI API key configured; voice casting will use the engine default")
            return []
        return [Voice(id=vid, name=label) for vid, label in _VOICES]

    def synthesize(self, text: str, out_path: str, *, rate: int = DEFAULT_RATE, voice_id: str | None = None) -> None:
        client = self._get_client()
        if client is None:
            raise RuntimeError(
                "OpenAI TTS is not configured: set MYVIDCRAFT_OPENAI_API_KEY (or OPENAI_API_KEY) "
                "and install the `openai` package"
            )

        # An unknown id must not fail the turn — losing one voice assignment is a
        # worse episode, not a broken one.
        voice = voice_id if voice_id in _VOICE_IDS else DEFAULT_VOICE
        if voice_id and voice_id not in _VOICE_IDS:
            log.warning("unknown voice %r; falling back to %s", voice_id, DEFAULT_VOICE)

        from apps.api.settings import settings

        target = Path(out_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        response = client.audio.speech.create(
            model=settings.tts_model,
            voice=voice,
            input=text,
            response_format="wav",
            speed=rate_to_speed(rate),
        )
        # `write_to_file` streams without holding the whole clip in memory.
        response.write_to_file(str(target))
        _assert_audible(target)
