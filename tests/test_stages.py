"""The stage/progress model."""

from __future__ import annotations

import pytest

from packages.core.myvidcraft_core.stages import (
    PIPELINE_STAGES,
    STAGE_WEIGHTS,
    STARTING_PROGRESS,
    VOICE_RERUN_STAGES,
    Stage,
    progress_at,
    stage_bounds,
)


def test_pipeline_covers_the_whole_bar():
    first_start, _ = stage_bounds(PIPELINE_STAGES[0])
    _, last_end = stage_bounds(PIPELINE_STAGES[-1])

    assert first_start == pytest.approx(STARTING_PROGRESS)
    assert last_end == pytest.approx(1.0)


def test_stages_are_contiguous_and_ordered():
    """No gaps and no overlaps: each stage begins exactly where the last ended."""
    previous_end = STARTING_PROGRESS
    for stage in PIPELINE_STAGES:
        start, end = stage_bounds(stage)
        assert start == pytest.approx(previous_end)
        assert end > start
        previous_end = end


def test_progress_is_monotonic_within_a_stage():
    stage = Stage.voice_turns
    values = [progress_at(stage, i / 10) for i in range(11)]

    assert values == sorted(values)
    assert values[0] == pytest.approx(stage_bounds(stage)[0])
    assert values[-1] == pytest.approx(stage_bounds(stage)[1])


def test_fraction_is_clamped():
    """A task that miscounts its own items must not drive the bar out of its lane."""
    start, end = stage_bounds(Stage.dialogue_script)

    assert progress_at(Stage.dialogue_script, -5.0) == pytest.approx(start)
    assert progress_at(Stage.dialogue_script, 99.0) == pytest.approx(end)


def test_weights_decide_the_share_of_the_bar():
    """TTS is the slow stage, so it must own the largest span."""
    spans = {s: (lambda b: b[1] - b[0])(stage_bounds(s)) for s in PIPELINE_STAGES}

    assert max(spans, key=spans.get) is Stage.voice_turns
    total = sum(spans.values())
    for stage, span in spans.items():
        assert span / total == pytest.approx(STAGE_WEIGHTS[stage] / sum(STAGE_WEIGHTS.values()))


def test_lifecycle_stages_have_no_progress_weight():
    for stage in (Stage.queued, Stage.starting, Stage.completed, Stage.failed, Stage.cancelled):
        with pytest.raises(ValueError, match="lifecycle stage"):
            stage_bounds(stage)


def test_voice_rerun_is_a_suffix_of_the_pipeline():
    """A rerun must resume mid-pipeline, not run a divergent stage list."""
    assert PIPELINE_STAGES[-len(VOICE_RERUN_STAGES) :] == VOICE_RERUN_STAGES


def test_stage_values_are_engine_namespaced():
    """Stage names carry the owning engine, not the build-order phase number."""
    for stage in PIPELINE_STAGES:
        assert "." in stage.value
        assert "phase" not in stage.value
