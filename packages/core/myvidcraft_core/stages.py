"""
Pipeline stages and the progress model derived from them.

Why this exists
---------------
Stage names used to be bare string literals repeated in each task: once in the
`publish()` event and again in the `_update_run(stage=...)` write. Nothing tied
the two together, so a typo in one produced a run whose reported stage silently
disagreed with its stored stage.

Progress was worse: eight hand-tuned floats spread across the task bodies, with
the ordering held only in the author's head. Inserting a stage meant recomputing
every float downstream of it by hand.

Here a stage declares a *relative weight* instead, and the bounds are derived.
Adding a stage is one line; the bar rebalances itself. The video stage was added
that way, and the ASR and clip stages will be.
"""

from __future__ import annotations

from enum import Enum

# The run is marked started before the first stage does any work, so the bar
# moves off zero immediately. The pipeline proper occupies (STARTING, 1.0].
STARTING_PROGRESS = 0.01


class Stage(str, Enum):
    """
    Every value `Run.stage` can hold.

    Persisted to the database and shown in the UI. Runs created before this enum
    existed hold free-form strings, which is why `RunOut.stage` stays a plain
    `str` — old rows must remain readable.
    """

    # Lifecycle (not pipeline work, no weight).
    queued = "queued"
    starting = "starting"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"

    # Pipeline work, in execution order.
    brain_strategy = "brain.generate_strategy"
    dialogue_script = "dialogue.generate_script"
    voice_turns = "voice.generate_turn_audio"
    audio_master = "audio.stitch_master"
    video_compose = "video.compose_episode"


# Relative cost of each stage. These are wall-clock proportions, not percentages:
# only the ratios matter, so a new stage can be added without touching the others.
#
# Calibrated against local runs — TTS dominates because it drives a real speech
# engine turn by turn, while the brain and script stages are one LLM call each.
STAGE_WEIGHTS: dict[Stage, float] = {
    Stage.brain_strategy: 1.0,
    Stage.dialogue_script: 2.0,
    Stage.voice_turns: 5.0,
    Stage.audio_master: 1.0,
    # Draws a bounded number of still frames (one per caption) and hands the mux
    # to FFmpeg, so it costs far less than the per-turn synthesis above. A neural
    # avatar backend registered against the same seam would be the heaviest stage
    # in the pipeline; re-weight here when one lands, and the bar rebalances.
    Stage.video_compose: 2.0,
}

#: Pipeline stages in execution order.
PIPELINE_STAGES: tuple[Stage, ...] = tuple(STAGE_WEIGHTS)

#: The subset a voice-only rerun executes. New audio invalidates the video that
#: was cut against it, so the compositor runs again rather than leaving a stale
#: episode.mp4 next to freshly rendered speech.
VOICE_RERUN_STAGES: tuple[Stage, ...] = (Stage.voice_turns, Stage.audio_master, Stage.video_compose)

#: The subset a video-only rerun executes: re-cut the existing audio.
VIDEO_RERUN_STAGES: tuple[Stage, ...] = (Stage.video_compose,)

TERMINAL_STAGES: frozenset[Stage] = frozenset({Stage.completed, Stage.failed, Stage.cancelled})


def _bounds() -> dict[Stage, tuple[float, float]]:
    total = sum(STAGE_WEIGHTS.values())
    span = 1.0 - STARTING_PROGRESS
    out: dict[Stage, tuple[float, float]] = {}
    cursor = 0.0
    for stage, weight in STAGE_WEIGHTS.items():
        start = STARTING_PROGRESS + span * (cursor / total)
        cursor += weight
        end = STARTING_PROGRESS + span * (cursor / total)
        out[stage] = (start, end)
    return out


_BOUNDS = _bounds()


def stage_bounds(stage: Stage) -> tuple[float, float]:
    """The (start, end) progress values a stage moves the bar between."""
    try:
        return _BOUNDS[stage]
    except KeyError:
        raise ValueError(f"{stage} is a lifecycle stage and carries no progress weight") from None


def progress_at(stage: Stage, fraction: float = 1.0) -> float:
    """
    Progress after completing `fraction` of `stage`.

    `fraction` is clamped, so a task that miscounts its own items cannot drive
    the bar backwards or past the next stage.
    """
    start, end = stage_bounds(stage)
    fraction = max(0.0, min(1.0, fraction))
    return start + (end - start) * fraction
