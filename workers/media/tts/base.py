"""
The speech-synthesis seam.

Phase 2 shipped against the local system engine (SAPI via pyttsx3) because it is
free, offline and always installed on Windows. The Tier-1 plan calls for Fish
Speech or XTTS later, and that swap is the reason this protocol exists: the
orchestrator resolves an engine by name and never imports a concrete one.

Deliberately small. An engine has to enumerate its voices and render text to a
WAV file; everything else — casting, pacing, stitching, mastering — is engine
independent and already lives elsewhere. Widening this interface to fit one
provider's extras would push provider-specific concepts into the orchestrator,
which is exactly what it exists to prevent.

The WAV contract matters: `stitch_turns_to_episode` requires every turn in a run
to share channel count, sample width and frame rate. An engine must be internally
consistent across calls; it does not need to match any other engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Voice:
    """A voice an engine can render with. `id` is opaque and engine-scoped."""

    id: str
    name: str


@runtime_checkable
class SpeechEngine(Protocol):
    """What the orchestrator needs from any text-to-speech backend."""

    #: Stable identifier, matching the key this engine is registered under.
    name: str

    def list_voices(self) -> list[Voice]:
        """
        Voices available right now. Returns `[]` rather than raising when the
        backend is unreachable — voice casting degrades to the engine default,
        which is a worse episode but still an episode.
        """
        ...

    def synthesize(self, text: str, out_path: str, *, rate: int, voice_id: str | None = None) -> None:
        """
        Render `text` to a WAV at `out_path`, creating parent directories.

        `rate` is words per minute. `voice_id` is an id from `list_voices()`;
        `None` means the engine default. An unknown id should fall back to the
        default rather than raise — losing a voice assignment must not fail a run.

        Raises only when no audio could be produced at all.
        """
        ...
