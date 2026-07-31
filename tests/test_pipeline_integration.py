"""
End-to-end run of the local pipeline.

This is the "tiny run" the plan has carried as an open item since Phase 1. It was
impractical while the orchestrator imported the system speech engine directly —
a real run drives SAPI for minutes. With synthesis behind `SpeechEngine`, a fake
engine that writes 20ms of silence makes the whole chain testable in milliseconds.

The LLM is disabled globally in conftest, so the brain and dialogue engines take
their deterministic template paths. Everything downstream is the real code.
"""

from __future__ import annotations

import json
import struct
import wave
from pathlib import Path

import pytest
from sqlmodel import Session, select

from apps.api.db import create_db_and_tables, engine
from apps.api.models import Artifact, Project, Run
from packages.core.myvidcraft_core.stages import Stage
from workers.media.tts import registry
from workers.media.tts.base import Voice

FRAMERATE = 22050

#: The compositor draws in pure Python, so a full-size frame costs real time for
#: no extra coverage. Every band in the layout derives from the frame size.
TEST_FRAME = (320, 180)


class SilentEngine:
    """A `SpeechEngine` that writes valid, consistent, near-empty WAVs."""

    name = "silent-test"

    def __init__(self) -> None:
        self.synthesized: list[tuple[str, str | None]] = []

    def list_voices(self) -> list[Voice]:
        return [
            Voice(id="test-david", name="Test David"),
            Voice(id="test-zira", name="Test Zira"),
        ]

    def synthesize(self, text: str, out_path: str, *, rate: int, voice_id: str | None = None) -> None:
        self.synthesized.append((text, voice_id))
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        with wave.open(out_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(FRAMERATE)
            w.writeframes(struct.pack("<h", 1200) * int(FRAMERATE * 0.02))


@pytest.fixture
def speech(monkeypatch):
    from apps.api.settings import settings

    fake = SilentEngine()
    registry.register(fake.name, lambda: fake)
    monkeypatch.setattr(settings, "tts_engine", fake.name)
    yield fake
    registry.reset()


@pytest.fixture(autouse=True)
def _small_frames(monkeypatch):
    from apps.api.settings import settings

    monkeypatch.setattr(settings, "video_width", TEST_FRAME[0])
    monkeypatch.setattr(settings, "video_height", TEST_FRAME[1])


@pytest.fixture
def run_id() -> str:
    create_db_and_tables()
    with Session(engine) as s:
        project = Project(name="Integration", niche="AI productivity", brand_profile_json={"tone": "confident"})
        s.add(project)
        s.commit()
        run = Run(project_id=project.id, status="queued", stage="queued", config_json={"mode": "teaching"})
        s.add(run)
        s.commit()
        return run.id


def _artifacts(run_id: str) -> dict[str, list[Artifact]]:
    with Session(engine) as s:
        rows = s.exec(select(Artifact).where(Artifact.run_id == run_id)).all()
    grouped: dict[str, list[Artifact]] = {}
    for a in rows:
        grouped.setdefault(a.type, []).append(a)
    return grouped


def test_a_run_produces_a_playable_episode(run_id, speech, artifacts_dir: Path):
    from workers.orchestrator.tasks import run_pipeline_local

    run_pipeline_local(run_id)

    with Session(engine) as s:
        run = s.get(Run, run_id)

    assert run.error is None
    assert run.status == "completed"
    assert run.stage == Stage.completed.value
    assert run.progress == pytest.approx(1.0)

    produced = _artifacts(run_id)
    for required in (
        "strategy", "episode_plan", "dialogue", "turn_audio_manifest",
        "episode_audio_wav", "episode_timeline", "episode_captions_srt", "video_storyboard",
    ):
        assert required in produced, f"missing {required} artifact"

    episode = Path(produced["episode_audio_wav"][0].path)
    assert episode.exists()
    with wave.open(str(episode), "rb") as w:
        assert w.getnframes() > 0
        assert w.getframerate() == FRAMERATE


def test_the_timeline_describes_the_audio_that_was_actually_written(run_id, speech):
    """
    The compositor cuts against these numbers rather than re-deriving them.

    The stitcher counts frames written, so the timeline has to describe the file
    on disk — millisecond arithmetic over the requested pauses would drift.
    """
    from workers.orchestrator.tasks import run_pipeline_local

    run_pipeline_local(run_id)

    timeline = json.loads(Path(_artifacts(run_id)["episode_timeline"][0].path).read_text(encoding="utf-8"))
    episode = Path(_artifacts(run_id)["episode_audio_wav"][0].path)
    with wave.open(str(episode), "rb") as w:
        actual_ms = round(w.getnframes() * 1000 / w.getframerate())

    assert timeline["duration_ms"] == actual_ms
    assert timeline["turn_count"] == len(timeline["turns"])

    turns = timeline["turns"]
    assert all(t["end_ms"] > t["start_ms"] for t in turns), "a turn covers no audio"
    for earlier, later in zip(turns, turns[1:]):
        assert earlier["end_ms"] <= later["start_ms"], "turns overlap in the episode"
    assert turns[-1]["end_ms"] <= actual_ms, "a turn runs past the end of the audio"


def test_the_run_produces_a_watchable_storyboard_and_captions(run_id, speech):
    """
    FFmpeg is optional, so the mp4 is asserted only when it could have been made.

    The storyboard and captions are real output either way — that is what makes a
    missing dependency a degradation rather than a failed run.
    """
    from workers.media.ffmpeg import ffmpeg_available
    from workers.orchestrator.tasks import run_pipeline_local

    run_pipeline_local(run_id)
    produced = _artifacts(run_id)

    storyboard = json.loads(Path(produced["video_storyboard"][0].path).read_text(encoding="utf-8"))
    assert storyboard["engine"] == "slideshow"
    assert storyboard["scenes"], "the episode was cut into no scenes"
    assert (storyboard["width"], storyboard["height"]) == TEST_FRAME

    scenes = storyboard["scenes"]
    assert scenes[0]["start_ms"] == 0
    for earlier, later in zip(scenes, scenes[1:]):
        assert earlier["end_ms"] == later["start_ms"], "a gap opened in the cut"

    srt = Path(produced["episode_captions_srt"][0].path).read_text(encoding="utf-8")
    assert srt.startswith("1\n")
    assert "-->" in srt

    if ffmpeg_available():
        assert "episode_video_mp4" in produced
        assert Path(produced["episode_video_mp4"][0].path).exists()
    else:
        assert "episode_video_mp4" not in produced
        assert storyboard["notes"], "the reason for the missing mp4 must be recorded"


def test_frames_are_discarded_once_they_have_been_muxed(run_id, speech, artifacts_dir: Path):
    """
    Frames are uncompressed PPM and are by far the largest thing a run writes.

    They are an intermediate when an mp4 exists and the deliverable when one does
    not, so the storyboard says which of the two happened rather than pointing at
    files that may have been deleted.
    """
    from workers.media.ffmpeg import ffmpeg_available
    from workers.orchestrator.tasks import run_pipeline_local

    run_pipeline_local(run_id)

    storyboard = json.loads(
        Path(_artifacts(run_id)["video_storyboard"][0].path).read_text(encoding="utf-8")
    )
    leftover = list((artifacts_dir / run_id / "frames").glob("*.ppm"))

    if ffmpeg_available():
        assert storyboard["frames_kept"] is False
        assert leftover == [], "muxed frames were left on disk"
        assert all("frame" not in scene for scene in storyboard["scenes"])
    else:
        # No mp4 means the storyboard is the output; the frames have to survive.
        assert storyboard["frames_kept"] is True
        assert leftover, "the storyboard lost the frames it describes"
        assert all(Path(scene["frame"]).exists() for scene in storyboard["scenes"])


def test_a_video_rerun_recuts_the_episode_without_touching_the_audio(run_id, speech):
    """The cheapest re-render: iterate on the look without paying for TTS again."""
    from workers.orchestrator.tasks import run_pipeline_local, run_video_pipeline_local

    run_pipeline_local(run_id)
    before = _artifacts(run_id)
    audio_ids = {a.id for a in before["turn_audio_wav"] + before["episode_audio_wav"]}
    first_storyboard = {a.id for a in before["video_storyboard"]}
    synthesized_before = len(speech.synthesized)

    run_video_pipeline_local(run_id)
    after = _artifacts(run_id)

    assert len(speech.synthesized) == synthesized_before, "a re-cut re-ran the speech engine"
    assert {a.id for a in after["turn_audio_wav"] + after["episode_audio_wav"]} == audio_ids
    assert {a.id for a in after["video_storyboard"]}.isdisjoint(first_storyboard)
    assert len(after["video_storyboard"]) == 1, "storyboards stacked instead of being replaced"

    with Session(engine) as s:
        assert s.get(Run, run_id).status == "completed"


def test_captions_can_be_switched_off_for_a_recut(run_id, speech):
    from workers.orchestrator.tasks import run_pipeline_local, run_video_pipeline_local

    run_pipeline_local(run_id)

    with Session(engine) as s:
        run = s.get(Run, run_id)
        run.config_json = {**(run.config_json or {}), "video_captions": False}
        s.add(run)
        s.commit()

    run_video_pipeline_local(run_id)
    produced = _artifacts(run_id)

    assert "episode_captions_srt" not in produced, "captions were written despite being off"
    storyboard = json.loads(Path(produced["video_storyboard"][0].path).read_text(encoding="utf-8"))
    # With captions off the cut falls back to one scene per turn.
    assert all(scene["lines"] == [] for scene in storyboard["scenes"])


def test_every_spoken_turn_is_synthesized_and_registered(run_id, speech):
    from workers.orchestrator.tasks import run_pipeline_local

    run_pipeline_local(run_id)

    turns = json.loads((Path(_artifacts(run_id)["dialogue"][0].path)).read_text(encoding="utf-8"))
    spoken = [t for t in turns if (t.get("text") or "").strip()]

    assert len(speech.synthesized) == len(spoken)
    assert len(_artifacts(run_id)["turn_audio_wav"]) == len(spoken)


def test_speakers_are_cast_to_distinct_voices(run_id, speech):
    from workers.orchestrator.tasks import run_pipeline_local

    run_pipeline_local(run_id)

    manifest = json.loads(Path(_artifacts(run_id)["turn_audio_manifest"][0].path).read_text(encoding="utf-8"))
    voice_ids = {v["voice_id"] for v in manifest["voices"].values()}

    assert manifest["engine"] == speech.name
    assert len(manifest["voices"]) == 2, "a two-speaker episode should cast two speakers"
    assert len(voice_ids) == 2, "host and guest must not share a voice"


def test_a_voice_rerun_replaces_audio_but_keeps_the_script(run_id, speech):
    from workers.orchestrator.tasks import run_pipeline_local, run_voice_pipeline_local

    run_pipeline_local(run_id)
    before = _artifacts(run_id)
    script_ids = {a.id for a in before["dialogue"] + before["episode_plan"] + before["strategy"]}
    first_wav_ids = {a.id for a in before["turn_audio_wav"]}

    run_voice_pipeline_local(run_id)
    after = _artifacts(run_id)

    # Script artifacts are the same rows; audio rows are new ones.
    assert {a.id for a in after["dialogue"] + after["episode_plan"] + after["strategy"]} == script_ids
    assert {a.id for a in after["turn_audio_wav"]}.isdisjoint(first_wav_ids)
    assert len(after["turn_audio_wav"]) == len(before["turn_audio_wav"]), "audio stacked instead of being replaced"

    with Session(engine) as s:
        assert s.get(Run, run_id).status == "completed"


def test_progress_never_moves_backwards(run_id, speech, monkeypatch):
    """The bar is derived from stage weights, so it must rise monotonically."""
    from workers.orchestrator import tasks

    seen: list[float] = []
    original = tasks._update_run

    def spy(rid, **updates):
        if "progress" in updates:
            seen.append(updates["progress"])
        return original(rid, **updates)

    monkeypatch.setattr(tasks, "_update_run", spy)
    tasks.run_pipeline_local(run_id)

    assert seen == sorted(seen), f"progress went backwards: {seen}"
    assert seen[-1] == pytest.approx(1.0)
