"""
The video-rendering seam.

Phase 3 ships against a compositor that draws frames in pure Python (see
`slideshow.py`), for the same reason Phase 2 shipped against the system speech
engine: it is free, offline, and works on a machine with no GPU. The Tier-1 plan
calls for MuseTalk with a LivePortrait fallback, and that swap is why this
protocol exists — the orchestrator resolves a renderer by name and never imports
a concrete one.

The interface is narrow on purpose. A renderer receives an `EpisodeVideoSpec`
(audio, timeline, captions, look) and returns a `RenderResult` describing what it
managed to produce. Everything upstream of that — timeline extraction, caption
segmentation, artifact registration, progress — is renderer independent and
already lives elsewhere.

`available()` is the part that earns its keep. Video backends have heavy external
requirements (FFmpeg here, CUDA and model weights for a neural one), and the
project's rule is that a missing optional dependency degrades a run instead of
failing it. A renderer reports what it can do *before* the stage commits to it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from .captions import Caption

RGB = tuple[int, int, int]


@dataclass(frozen=True)
class SpeakerStyle:
    """How one speaker is presented on screen."""

    name: str
    role: str = "host"
    accent: RGB = (99, 102, 241)


@dataclass(frozen=True)
class EpisodeVideoSpec:
    """Everything a renderer needs to cut one episode."""

    run_id: str
    title: str
    audio_path: str
    #: Timeline entries: index, speaker, text, start_ms, end_ms, broll_cues.
    turns: list[dict]
    captions: list[Caption]
    speakers: dict[str, SpeakerStyle]
    out_dir: str
    width: int = 1280
    height: int = 720
    fps: int = 25
    duration_ms: int = 0
    show_captions: bool = True
    show_broll_slots: bool = True


@dataclass
class RenderResult:
    """
    What a render produced.

    `video_path` is None when the backend could not mux — on this machine that
    means FFmpeg is absent. The storyboard and frames are still real output, so
    the stage registers them and the run completes; `notes` carries the reason
    through to the run's event stream.
    """

    video_path: str | None = None
    frame_paths: list[str] = field(default_factory=list)
    scenes: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@runtime_checkable
class VideoRenderer(Protocol):
    """What the orchestrator needs from any video backend."""

    #: Stable identifier, matching the key this renderer is registered under.
    name: str

    def available(self) -> tuple[bool, str]:
        """
        Whether a full render is possible right now, and why not if it is not.

        Returning `(False, reason)` must not mean "do nothing" — a renderer is
        still expected to emit whatever partial output it can. The reason is
        surfaced on the run so a missing dependency is diagnosable from the UI
        instead of from a worker log.
        """
        ...

    def render(self, spec: EpisodeVideoSpec) -> RenderResult:
        """
        Compose the episode described by `spec`.

        Raises only when nothing at all could be produced.
        """
        ...
