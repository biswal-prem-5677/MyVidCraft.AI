"""
The reliability properties the plan claims for the task graph.

Three things the plan promised and the code did not do, which is why they are
pinned here rather than left to the stages that happen to exercise them:

- **Idempotent registration.** A stage can run twice for one run -- a retry, or a
  rerun -- and the artifact table has to converge rather than accumulate.
- **Retries scoped to transient failures.** Retrying a schema error turns an
  instant, legible failure into a slow one, and retrying a cancellation fights
  the user. The scope is narrower than it looks — see
  `test_what_the_policy_deliberately_does_not_cover`.
- **`stage.progress` events.** The bar used to advance in the database with
  nothing on the wire, so a client watching the websocket saw silence across the
  multi-minute voice stage.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session, select

from apps.api.db import create_db_and_tables, engine
from apps.api.models import Artifact, Run
from packages.core.myvidcraft_core.stages import Stage, progress_at
from workers.orchestrator import tasks


@pytest.fixture(autouse=True)
def _tables():
    create_db_and_tables()


@pytest.fixture
def run_id() -> str:
    with Session(engine) as s:
        run = Run(project_id="p1", status="running", stage="queued", config_json={})
        s.add(run)
        s.commit()
        return run.id


def _rows(run_id: str, type_: str) -> list[Artifact]:
    with Session(engine) as s:
        return list(s.exec(select(Artifact).where(Artifact.run_id == run_id, Artifact.type == type_)).all())


# -- idempotent artifact registration ---------------------------------------


def test_registering_the_same_file_twice_keeps_one_row(run_id):
    """A retried stage must converge, not leave the UI showing the file twice."""
    tasks._create_artifact(run_id, "strategy", "/a/strategy.json", {"topics": 1})
    tasks._create_artifact(run_id, "strategy", "/a/strategy.json", {"topics": 2})

    rows = _rows(run_id, "strategy")
    assert len(rows) == 1
    assert rows[0].metadata_json == {"topics": 2}, "the newer attempt should win"


def test_re_registering_refreshes_the_timestamp(run_id):
    tasks._create_artifact(run_id, "strategy", "/a/strategy.json")
    first = _rows(run_id, "strategy")[0].created_at

    tasks._create_artifact(run_id, "strategy", "/a/strategy.json")

    assert _rows(run_id, "strategy")[0].created_at >= first


def test_distinct_files_of_the_same_type_stay_separate(run_id):
    """Per-turn WAVs share a type and must not collapse into one row."""
    for i in range(3):
        tasks._create_artifact(run_id, "turn_audio_wav", f"/a/turns/turn_{i:04d}.wav", {"turn_index": i})

    assert len(_rows(run_id, "turn_audio_wav")) == 3


def test_the_same_path_under_a_different_type_is_a_different_artifact(run_id):
    tasks._create_artifact(run_id, "episode_audio_wav", "/a/episode.wav")
    tasks._create_artifact(run_id, "turn_audio_wav", "/a/episode.wav")

    assert len(_rows(run_id, "episode_audio_wav")) == 1
    assert len(_rows(run_id, "turn_audio_wav")) == 1


def test_runs_do_not_share_artifact_rows(run_id):
    with Session(engine) as s:
        other = Run(project_id="p1", status="running", stage="queued", config_json={})
        s.add(other)
        s.commit()
        other_id = other.id

    tasks._create_artifact(run_id, "strategy", "/a/strategy.json")
    tasks._create_artifact(other_id, "strategy", "/a/strategy.json")

    assert len(_rows(run_id, "strategy")) == 1
    assert len(_rows(other_id, "strategy")) == 1


# -- retry policy -----------------------------------------------------------


def test_transient_io_errors_are_retryable():
    """Filesystem faults — a full disk, a scanner holding a WAV open — are OSError."""
    for exc in (OSError, ConnectionError, TimeoutError, PermissionError):
        assert issubclass(exc, tasks.RETRYABLE_ERRORS)


def test_what_the_policy_deliberately_does_not_cover():
    """
    The obvious guesses about which failures are transient are wrong, and the
    comment on RETRYABLE_ERRORS says so. Pinned here so it stays true.

    A SQLite lock and a SAPI/COM fault both look like textbook transient errors
    and neither is an OSError, so neither is retried by this policy. FFmpeg never
    raises at all — it is caught at the call site and downgraded to a False.
    """
    import sqlalchemy.exc

    assert not issubclass(sqlalchemy.exc.OperationalError, tasks.RETRYABLE_ERRORS)

    pywintypes = pytest.importorskip("pywintypes")
    assert not issubclass(pywintypes.com_error, tasks.RETRYABLE_ERRORS)


def test_a_cancelled_run_is_never_retried():
    """Cancellation is a user action; retrying it fights the user."""
    assert not issubclass(tasks.RunCancelled, tasks.RETRYABLE_ERRORS)


def test_deterministic_failures_are_not_retried():
    """These fail identically every attempt, so a retry only slows the report."""
    for exc in (ValueError, KeyError, TypeError, RuntimeError):
        assert not issubclass(exc, tasks.RETRYABLE_ERRORS)


def test_the_retry_policy_is_bounded_and_backs_off():
    policy = tasks.TASK_RETRY_POLICY

    assert policy["max_retries"] == 2, "an unbounded retry would hide a real outage"
    assert policy["retry_backoff"] >= 1
    assert policy["retry_backoff_max"] <= 60, "a long backoff stalls the whole chain"
    assert policy["retry_jitter"] is True


@pytest.mark.parametrize(
    "task_name",
    [
        "brain.generate_strategy",
        "dialogue.generate_script",
        "voice.generate_turn_audio",
        "audio.stitch_master",
        "video.compose_episode",
    ],
)
def test_every_io_stage_carries_the_retry_policy(task_name):
    task = tasks.celery_app.tasks[task_name]

    assert task.max_retries == tasks.TASK_RETRY_POLICY["max_retries"]
    assert task.autoretry_for == tasks.RETRYABLE_ERRORS


@pytest.mark.parametrize("task_name", ["run.mark_running", "run.mark_completed"])
def test_lifecycle_tasks_are_not_retried(task_name):
    """These are pure DB writes; the chain's errback already covers them."""
    assert not getattr(tasks.celery_app.tasks[task_name], "autoretry_for", ())


# -- stage.progress events --------------------------------------------------


def test_progress_inside_a_stage_reaches_the_event_stream(run_id, monkeypatch):
    events: list[dict] = []
    monkeypatch.setattr(tasks, "publish", lambda rid, ev: events.append(ev))

    tasks._report_progress(run_id, Stage.voice_turns, 0.5)

    assert len(events) == 1
    assert events[0]["type"] == "stage.progress"
    assert events[0]["stage"] == Stage.voice_turns.value
    assert events[0]["fraction"] == 0.5
    assert events[0]["progress"] == pytest.approx(progress_at(Stage.voice_turns, 0.5))


def test_progress_is_persisted_as_well_as_published(run_id, monkeypatch):
    monkeypatch.setattr(tasks, "publish", lambda rid, ev: None)

    tasks._report_progress(run_id, Stage.voice_turns, 0.25)

    with Session(engine) as s:
        assert s.get(Run, run_id).progress == pytest.approx(progress_at(Stage.voice_turns, 0.25))


def test_a_miscounted_fraction_cannot_escape_the_stage(run_id, monkeypatch):
    """`progress_at` clamps, so a stage that miscounts cannot drive the bar past itself."""
    events: list[dict] = []
    monkeypatch.setattr(tasks, "publish", lambda rid, ev: events.append(ev))

    tasks._report_progress(run_id, Stage.voice_turns, 5.0)
    tasks._report_progress(run_id, Stage.voice_turns, -3.0)

    assert events[0]["progress"] == pytest.approx(progress_at(Stage.voice_turns, 1.0))
    assert events[1]["progress"] == pytest.approx(progress_at(Stage.voice_turns, 0.0))
    assert 0.0 <= events[1]["fraction"] <= 1.0
