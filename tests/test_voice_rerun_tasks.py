"""Orchestrator mechanics behind a voice-only rerun."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlmodel import Session, select

from apps.api.db import create_db_and_tables, engine
from apps.api.models import Artifact, Run
from workers.orchestrator import tasks


@pytest.fixture(autouse=True)
def _tables():
    create_db_and_tables()


@pytest.fixture
def run_with_audio(artifacts_dir: Path):
    """A finished run with a script, per-turn WAVs and a mastered episode on disk."""
    with Session(engine) as session:
        run = Run(project_id="p1", status="completed", stage="completed", progress=1.0, config_json={})
        session.add(run)
        session.commit()
        run_id = run.id

    base = artifacts_dir / run_id
    (base / "turns").mkdir(parents=True, exist_ok=True)

    dialogue = base / "dialogue_turns.json"
    dialogue.write_text(json.dumps([{"speaker": "Ada", "text": "Hi."}]), encoding="utf-8")

    files = {
        "dialogue": dialogue,
        "turn_audio_wav": base / "turns" / "turn_0000_ada.wav",
        "turn_audio_manifest": base / "turns" / "manifest.json",
        "episode_audio_wav": base / "episode.wav",
        "episode_audio_mp3": base / "episode.mp3",
    }
    for type_, path in files.items():
        if type_ != "dialogue":
            path.write_bytes(b"audio")
        with Session(engine) as session:
            session.add(Artifact(run_id=run_id, type=type_, path=str(path), metadata_json={}))
            session.commit()

    return run_id, files


def _artifact_types(run_id: str) -> list[str]:
    with Session(engine) as session:
        rows = session.exec(select(Artifact).where(Artifact.run_id == run_id)).all()
        return sorted(a.type for a in rows)


def test_purge_removes_only_the_audio_artifacts(run_with_audio):
    run_id, files = run_with_audio

    removed = tasks._purge_voice_artifacts(run_id)

    assert removed == 4
    assert _artifact_types(run_id) == ["dialogue"]
    assert files["dialogue"].exists()
    for key in ("turn_audio_wav", "turn_audio_manifest", "episode_audio_wav", "episode_audio_mp3"):
        assert not files[key].exists()


def test_purge_is_safe_to_repeat(run_with_audio):
    run_id, _ = run_with_audio

    tasks._purge_voice_artifacts(run_id)

    assert tasks._purge_voice_artifacts(run_id) == 0


def test_purge_survives_a_file_that_is_already_gone(run_with_audio):
    run_id, files = run_with_audio
    files["episode_audio_wav"].unlink()

    assert tasks._purge_voice_artifacts(run_id) == 4
    assert _artifact_types(run_id) == ["dialogue"]


def test_prepare_resets_a_completed_run_to_running(run_with_audio):
    run_id, _ = run_with_audio

    tasks.prepare_voice_rerun(run_id)

    with Session(engine) as session:
        run = session.get(Run, run_id)

    assert run.status == "running"
    assert run.stage == "tts_generate_turn_audio"
    assert run.completed_at is None
    assert run.progress == pytest.approx(0.55)


def test_prepare_clears_a_previous_failure(run_with_audio):
    run_id, _ = run_with_audio
    tasks._update_run(run_id, status="failed", error="RuntimeError: TTS produced no audio")

    tasks.prepare_voice_rerun(run_id)

    with Session(engine) as session:
        assert session.get(Run, run_id).error is None


def test_prepare_refuses_a_run_with_no_script(artifacts_dir: Path):
    with Session(engine) as session:
        run = Run(project_id="p1", status="completed", stage="completed", progress=1.0, config_json={})
        session.add(run)
        session.commit()
        run_id = run.id

    with pytest.raises(RuntimeError, match="no dialogue script"):
        tasks.prepare_voice_rerun(run_id)


def test_prepare_refuses_a_deleted_run():
    with pytest.raises(tasks.RunCancelled):
        tasks.prepare_voice_rerun("does-not-exist")


def test_safe_unlink_refuses_paths_outside_the_artifacts_dir(tmp_path):
    outsider = tmp_path / "important.txt"
    outsider.write_text("keep me", encoding="utf-8")

    assert tasks._safe_unlink(str(outsider)) is False
    assert outsider.exists()


def test_voice_overrides_are_read_from_the_run_config():
    with Session(engine) as session:
        run = Run(
            project_id="p1",
            status="completed",
            stage="completed",
            config_json={"mode": "teaching", "voice_overrides": {"Ada": "VOICE_A", "Ben": ""}},
        )
        session.add(run)
        session.commit()
        run_id = run.id

    # Blank ids are dropped so an untouched select cannot pin an empty voice.
    assert tasks._voice_overrides(run_id) == {"Ada": "VOICE_A"}


def test_missing_or_malformed_overrides_degrade_to_empty():
    with Session(engine) as session:
        run = Run(project_id="p1", status="completed", stage="completed", config_json={"voice_overrides": "nope"})
        session.add(run)
        session.commit()
        run_id = run.id

    assert tasks._voice_overrides(run_id) == {}
    assert tasks._voice_overrides("does-not-exist") == {}


def test_voice_rerun_skips_the_script_stages():
    """The rerun chain must not touch the brain or script stages."""
    calls: list[str] = []

    def recorder(name):
        return lambda run_id: calls.append(name)

    stages = (
        recorder("prepare"),
        recorder("tts"),
        recorder("stitch"),
        recorder("complete"),
    )
    tasks._run_stages_local("run-x", stages)

    assert calls == ["prepare", "tts", "stitch", "complete"]


def test_a_failing_stage_marks_the_run_failed(run_with_audio):
    run_id, _ = run_with_audio

    def boom(_run_id):
        raise RuntimeError("synthesis exploded")

    tasks._run_stages_local(run_id, (boom,))

    with Session(engine) as session:
        run = session.get(Run, run_id)

    assert run.status == "failed"
    assert "synthesis exploded" in run.error


def test_cancellation_is_not_a_failure(run_with_audio):
    run_id, _ = run_with_audio
    tasks._update_run(run_id, status="cancelled")

    def cancelled(_run_id):
        raise tasks.RunCancelled("cancelled")

    tasks._run_stages_local(run_id, (cancelled,))

    with Session(engine) as session:
        assert session.get(Run, run_id).status == "cancelled"
