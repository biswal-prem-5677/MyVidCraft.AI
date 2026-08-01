"""
Two runs at once.

This is the property the deployment work was for. The engine that shipped first
held a process-wide `threading.Lock` around synthesis, so a worker with sixteen
cores still rendered one turn at a time and a second customer simply waited. A
compose file and a Postgres would not have helped: the ceiling was in-process.

So these tests do not check that concurrency is *fast*. They check that it
*happens at all* — that two pipelines genuinely overlap in time, and that
nothing they share (the artifact table, the run rows, the artifacts directory)
gets crossed between them.

The fake engine records when each synthesis call enters and leaves. Overlapping
intervals are the evidence; without them the assertions on artifact separation
would pass just as happily against a fully serialised pipeline.
"""

from __future__ import annotations

import struct
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlmodel import Session, select

from apps.api.db import create_db_and_tables, engine
from apps.api.models import Artifact, Project, Run
from workers.media.tts import registry
from workers.media.tts.base import Voice

FRAMERATE = 22050
TEST_FRAME = (320, 180)

#: Long enough that two concurrent calls reliably overlap, short enough that the
#: suite stays fast. A serialised pipeline cannot produce overlap at any value.
_SYNTH_DELAY_S = 0.05


class TimestampingEngine:
    """A `SpeechEngine` that records the wall-clock span of every call."""

    name = "concurrency-test"

    def __init__(self) -> None:
        self.spans: list[tuple[float, float]] = []
        self._guard = threading.Lock()  # protects the list only, never synthesis

    def list_voices(self) -> list[Voice]:
        return [Voice(id="a", name="Voice A"), Voice(id="b", name="Voice B")]

    def synthesize(self, text: str, out_path: str, *, rate: int, voice_id: str | None = None) -> None:
        started = time.monotonic()
        time.sleep(_SYNTH_DELAY_S)

        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        with wave.open(out_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(FRAMERATE)
            w.writeframes(struct.pack("<h", 1200) * int(FRAMERATE * 0.02))

        with self._guard:
            self.spans.append((started, time.monotonic()))


def _overlapping(spans: list[tuple[float, float]]) -> bool:
    """True if any two calls were in flight at the same moment."""
    for i, (a_start, a_end) in enumerate(spans):
        for b_start, b_end in spans[i + 1 :]:
            if a_start < b_end and b_start < a_end:
                return True
    return False


@pytest.fixture
def speech(monkeypatch):
    from apps.api.settings import settings

    fake = TimestampingEngine()
    registry.register(fake.name, lambda: fake)
    monkeypatch.setattr(settings, "tts_engine", fake.name)
    monkeypatch.setattr(settings, "video_width", TEST_FRAME[0])
    monkeypatch.setattr(settings, "video_height", TEST_FRAME[1])
    yield fake
    registry.reset()


def _make_run() -> str:
    create_db_and_tables()
    with Session(engine) as s:
        project = Project(name="Concurrency", niche="AI productivity", brand_profile_json={"tone": "confident"})
        s.add(project)
        s.commit()
        run = Run(project_id=project.id, status="queued", stage="queued", config_json={"mode": "teaching"})
        s.add(run)
        s.commit()
        return run.id


def _run_both(run_ids: list[str]) -> list:
    from workers.orchestrator.tasks import run_pipeline_local

    with ThreadPoolExecutor(max_workers=len(run_ids)) as pool:
        return [f.result() for f in [pool.submit(run_pipeline_local, r) for r in run_ids]]


@pytest.fixture
def two_completed_runs(speech):
    ids = [_make_run(), _make_run()]
    _run_both(ids)
    return ids


# -- the ceiling is gone ----------------------------------------------------


def test_synthesis_from_two_runs_actually_overlaps(two_completed_runs, speech):
    """
    The regression guard for the old engine's process-wide lock.

    If someone reintroduces a module-level mutex around synthesis, every other
    test still passes and only this one fails.
    """
    assert len(speech.spans) >= 4, "expected several turns across two runs"
    assert _overlapping(speech.spans), "synthesis was serialised — a global lock is back"


def test_both_runs_complete(two_completed_runs):
    with Session(engine) as s:
        runs = [s.get(Run, r) for r in two_completed_runs]

    for run in runs:
        assert run.status == "completed", f"run {run.id} ended {run.status}: {run.error}"
        assert run.error is None
        assert run.progress == pytest.approx(1.0)


# -- nothing shared gets crossed --------------------------------------------


def test_artifacts_do_not_leak_between_runs(two_completed_runs):
    first, second = two_completed_runs

    with Session(engine) as s:
        a = s.exec(select(Artifact).where(Artifact.run_id == first)).all()
        b = s.exec(select(Artifact).where(Artifact.run_id == second)).all()

    assert a and b
    assert {x.id for x in a}.isdisjoint({x.id for x in b})
    # Every path must sit under its own run directory, or one run's re-render
    # would delete the other's files.
    assert all(first in x.path for x in a), "an artifact was written outside its run dir"
    assert all(second in x.path for x in b)


def test_each_run_gets_a_full_artifact_set(two_completed_runs):
    """A dropped write under concurrency shows up as a missing artifact."""
    required = {
        "strategy",
        "episode_plan",
        "dialogue",
        "turn_audio_manifest",
        "episode_audio_wav",
        "episode_timeline",
    }

    for run_id in two_completed_runs:
        with Session(engine) as s:
            types = {a.type for a in s.exec(select(Artifact).where(Artifact.run_id == run_id)).all()}
        assert required <= types, f"run {run_id} is missing {required - types}"


def test_no_duplicate_artifact_rows_under_concurrency(two_completed_runs):
    """
    Registration is idempotent on (run, type, path).

    Two runs writing at once is exactly the case where a select-then-insert can
    interleave, so the invariant is worth asserting here and not only in the
    single-threaded test.
    """
    for run_id in two_completed_runs:
        with Session(engine) as s:
            rows = s.exec(select(Artifact).where(Artifact.run_id == run_id)).all()
        keys = [(r.type, r.path) for r in rows]
        assert len(keys) == len(set(keys)), f"duplicate artifact rows in {run_id}"


def test_audio_from_one_run_is_not_reused_by_the_other(two_completed_runs):
    """Both runs share a script template; their WAVs must still be separate files."""
    paths = []
    for run_id in two_completed_runs:
        with Session(engine) as s:
            rows = s.exec(
                select(Artifact).where(Artifact.run_id == run_id, Artifact.type == "episode_audio_wav")
            ).all()
        assert rows, f"run {run_id} produced no episode audio"
        paths.append(Path(rows[0].path).resolve())

    assert paths[0] != paths[1]
    assert all(p.exists() and p.stat().st_size > 0 for p in paths)
