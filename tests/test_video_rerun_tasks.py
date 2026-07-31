"""
Orchestrator mechanics behind a video-only re-render.

The counterpart to `test_voice_rerun_tasks.py`. What is specific to video is the
asymmetry between the two reruns: new audio invalidates the video that was cut
against it, so a voice rerun replaces the video too, while a video rerun must
leave every audio artifact alone. Getting that backwards leaves an `episode.mp4`
whose captions and cuts no longer match the WAV beside it.

Frames get their own cleanup because they are the largest thing a run writes and
the compositor names them by index — a re-render with fewer scenes would leave the
tail of the previous one on disk.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlmodel import Session, select

from apps.api.db import create_db_and_tables, engine
from apps.api.models import Artifact, Run
from packages.core.myvidcraft_core.stages import Stage, stage_bounds
from workers.orchestrator import tasks


@pytest.fixture(autouse=True)
def _tables():
    create_db_and_tables()


@pytest.fixture
def run_with_video(artifacts_dir: Path):
    """A finished run holding a script, audio, a timeline and a rendered video."""
    with Session(engine) as session:
        run = Run(project_id="p1", status="completed", stage="completed", progress=1.0, config_json={})
        session.add(run)
        session.commit()
        run_id = run.id

    base = artifacts_dir / run_id
    (base / "turns").mkdir(parents=True, exist_ok=True)
    (base / "frames").mkdir(parents=True, exist_ok=True)

    dialogue = base / "dialogue_turns.json"
    dialogue.write_text(json.dumps([{"speaker": "Ada", "text": "Hi."}]), encoding="utf-8")

    timeline = base / "episode_timeline.json"
    timeline.write_text(
        json.dumps({"duration_ms": 2000, "turn_count": 1,
                    "turns": [{"index": 0, "speaker": "Ada", "text": "Hi.", "start_ms": 0, "end_ms": 2000}]}),
        encoding="utf-8",
    )

    files = {
        "dialogue": dialogue,
        "turn_audio_wav": base / "turns" / "turn_0000_ada.wav",
        "turn_audio_manifest": base / "turns" / "manifest.json",
        "episode_audio_wav": base / "episode.wav",
        "episode_timeline": timeline,
        "episode_video_mp4": base / "episode.mp4",
        "episode_captions_srt": base / "captions.srt",
        "video_storyboard": base / "storyboard.json",
    }
    for type_, path in files.items():
        if type_ not in ("dialogue", "episode_timeline"):
            path.write_bytes(b"data")
        with Session(engine) as session:
            session.add(Artifact(run_id=run_id, type=type_, path=str(path), metadata_json={}))
            session.commit()

    frames = [base / "frames" / f"scene_{i:04d}.ppm" for i in range(3)]
    for frame in frames:
        frame.write_bytes(b"P6\n1 1\n255\n\x00\x00\x00")

    return run_id, files, frames


def _artifact_types(run_id: str) -> list[str]:
    with Session(engine) as session:
        rows = session.exec(select(Artifact).where(Artifact.run_id == run_id)).all()
        return sorted(a.type for a in rows)


# -- purging ----------------------------------------------------------------


def test_purge_removes_only_the_video_artifacts(run_with_video):
    """Audio is expensive to regenerate; a re-cut must not touch it."""
    run_id, files, _ = run_with_video

    removed = tasks._purge_video_artifacts(run_id)

    assert removed == 3
    assert _artifact_types(run_id) == [
        "dialogue", "episode_audio_wav", "episode_timeline", "turn_audio_manifest", "turn_audio_wav",
    ]
    for kept in ("dialogue", "episode_audio_wav", "episode_timeline", "turn_audio_wav"):
        assert files[kept].exists(), f"{kept} was deleted by a video purge"
    for gone in ("episode_video_mp4", "episode_captions_srt", "video_storyboard"):
        assert not files[gone].exists()


def test_a_voice_rerun_also_replaces_the_video_it_invalidated(run_with_video):
    """New audio makes the old cut stale: captions and cuts would not match."""
    run_id, files, _ = run_with_video

    tasks._purge_voice_artifacts(run_id)

    assert _artifact_types(run_id) == ["dialogue"]
    assert not files["episode_video_mp4"].exists()
    assert not files["episode_timeline"].exists()


def test_purge_is_safe_to_repeat(run_with_video):
    run_id, _, _ = run_with_video

    tasks._purge_video_artifacts(run_id)

    assert tasks._purge_video_artifacts(run_id) == 0


def test_purge_survives_a_file_that_is_already_gone(run_with_video):
    run_id, files, _ = run_with_video
    files["episode_video_mp4"].unlink()

    assert tasks._purge_video_artifacts(run_id) == 3


# -- frame cleanup ----------------------------------------------------------


def test_the_previous_renders_frames_are_cleared(run_with_video):
    """Frames are index-named, so a shorter re-render would leave a stale tail."""
    run_id, _, frames = run_with_video

    tasks._clear_frames_dir(run_id)

    assert not any(f.exists() for f in frames)


def test_clearing_frames_keeps_the_directory_itself(run_with_video, artifacts_dir: Path):
    run_id, _, _ = run_with_video

    tasks._clear_frames_dir(run_id)

    assert (artifacts_dir / run_id / "frames").is_dir()


def test_clearing_frames_is_a_no_op_when_there_are_none(artifacts_dir: Path):
    with Session(engine) as session:
        run = Run(project_id="p1", status="completed", stage="completed", config_json={})
        session.add(run)
        session.commit()
        run_id = run.id

    tasks._clear_frames_dir(run_id)  # must not raise


# -- preparing the rerun ----------------------------------------------------


def test_prepare_rewinds_a_completed_run_to_the_video_stage(run_with_video):
    run_id, _, _ = run_with_video

    tasks.prepare_video_rerun(run_id)

    with Session(engine) as session:
        run = session.get(Run, run_id)

    assert run.status == "running"
    assert run.stage == Stage.video_compose.value
    assert run.completed_at is None
    # Rewound only to the compositor: the bar must not claim the audio stages are
    # pending again, since they are not going to run.
    assert run.progress == pytest.approx(stage_bounds(Stage.video_compose)[0])


def test_prepare_clears_a_previous_failure(run_with_video):
    run_id, _, _ = run_with_video
    tasks._update_run(run_id, status="failed", error="RuntimeError: no scenes to render")

    tasks.prepare_video_rerun(run_id)

    with Session(engine) as session:
        assert session.get(Run, run_id).error is None


def test_prepare_refuses_a_run_with_no_timeline(artifacts_dir: Path):
    """The timeline is the compositor's only input contract."""
    with Session(engine) as session:
        run = Run(project_id="p1", status="completed", stage="completed", progress=1.0, config_json={})
        session.add(run)
        session.commit()
        run_id = run.id

    with pytest.raises(RuntimeError, match="no episode timeline"):
        tasks.prepare_video_rerun(run_id)


def test_prepare_refuses_a_deleted_run():
    with pytest.raises(tasks.RunCancelled):
        tasks.prepare_video_rerun("does-not-exist")


def test_prepare_purges_the_old_render_and_its_frames(run_with_video):
    run_id, files, frames = run_with_video

    tasks.prepare_video_rerun(run_id)

    assert not files["episode_video_mp4"].exists()
    assert not any(f.exists() for f in frames)
    assert files["episode_audio_wav"].exists(), "the audio must survive a re-cut"


# -- frame retention after a render ----------------------------------------


@pytest.fixture
def fake_renderer(monkeypatch):
    """
    A renderer whose mux always "succeeds", so the discard branch is reachable on
    a machine with no FFmpeg.
    """
    from apps.api.settings import settings
    from workers.media.video import registry
    from workers.media.video.base import RenderResult

    class Muxing:
        name = "fake-muxing"

        def available(self):
            return True, "ok"

        def render(self, spec):
            frames_dir = Path(spec.out_dir) / "frames"
            frames_dir.mkdir(parents=True, exist_ok=True)
            frames = []
            for i in range(2):
                frame = frames_dir / f"scene_{i:04d}.ppm"
                frame.write_bytes(b"P6\n1 1\n255\n\x00\x00\x00")
                frames.append(str(frame))

            out = Path(spec.out_dir) / "episode.mp4"
            out.write_bytes(b"\x00\x00\x00\x18ftypmp42")
            return RenderResult(
                video_path=str(out),
                frame_paths=frames,
                scenes=[
                    {"index": i, "start_ms": i * 1000, "end_ms": (i + 1) * 1000,
                     "speaker": "Ada", "lines": ["hi"], "broll_cues": [], "frame": frames[i]}
                    for i in range(2)
                ],
            )

    registry.register("fake-muxing", Muxing)
    monkeypatch.setattr(settings, "video_engine", "fake-muxing")
    yield
    registry.reset()


def test_muxed_frames_are_discarded_and_unlinked_from_the_storyboard(run_with_video, fake_renderer, artifacts_dir):
    """~2.7 MB each at 720p: keeping them after the mp4 exists leaks disk per run."""
    run_id, _, _ = run_with_video
    tasks._update_run(run_id, status="running")

    tasks.video_compose_episode(run_id)

    storyboard = json.loads((artifacts_dir / run_id / "storyboard.json").read_text(encoding="utf-8"))
    assert storyboard["frames_kept"] is False
    assert list((artifacts_dir / run_id / "frames").glob("*.ppm")) == []
    # The storyboard must not point at files that were just deleted.
    assert all("frame" not in scene for scene in storyboard["scenes"])
    assert (artifacts_dir / run_id / "episode.mp4").exists()


def test_a_render_without_an_mp4_keeps_the_frames_it_describes(run_with_video, monkeypatch, artifacts_dir):
    """Without a mux the storyboard is the deliverable, so its frames must survive."""
    from apps.api.settings import settings
    from workers.media.video import registry
    from workers.media.video.base import RenderResult

    class Storyboarding:
        name = "fake-storyboard"

        def available(self):
            return False, "ffmpeg not found on PATH"

        def render(self, spec):
            frames_dir = Path(spec.out_dir) / "frames"
            frames_dir.mkdir(parents=True, exist_ok=True)
            frame = frames_dir / "scene_0000.ppm"
            frame.write_bytes(b"P6\n1 1\n255\n\x00\x00\x00")
            return RenderResult(
                video_path=None,
                frame_paths=[str(frame)],
                scenes=[{"index": 0, "start_ms": 0, "end_ms": 1000, "speaker": "Ada",
                         "lines": [], "broll_cues": [], "frame": str(frame)}],
                notes=["ffmpeg not found on PATH; storyboard and captions only, no mp4"],
            )

    registry.register("fake-storyboard", Storyboarding)
    monkeypatch.setattr(settings, "video_engine", "fake-storyboard")
    run_id, _, _ = run_with_video
    tasks._update_run(run_id, status="running")

    try:
        tasks.video_compose_episode(run_id)
    finally:
        registry.reset()

    storyboard = json.loads((artifacts_dir / run_id / "storyboard.json").read_text(encoding="utf-8"))
    assert storyboard["frames_kept"] is True
    assert all(Path(scene["frame"]).exists() for scene in storyboard["scenes"])
    assert storyboard["notes"], "the reason for the missing mp4 must reach the storyboard"


# -- the rerun chain --------------------------------------------------------


def test_a_video_rerun_skips_every_stage_before_the_compositor():
    calls: list[str] = []

    def recorder(name):
        return lambda run_id: calls.append(name)

    tasks._run_stages_local("run-x", (recorder("prepare"), recorder("compose"), recorder("complete")))

    assert calls == ["prepare", "compose", "complete"]
