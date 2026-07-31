"""API surface of the "re-cut the video only" control."""

from __future__ import annotations

import json
from pathlib import Path


def _project(client):
    return client.post("/projects", json={"name": "P", "niche": "AI productivity"}).json()


def _run(client, project_id):
    return client.post(
        f"/projects/{project_id}/runs",
        json={"mode": "teaching", "platforms": ["youtube"], "language": "en", "config": {}},
    ).json()


def _write_timeline(artifacts_dir: Path, run_id: str) -> Path:
    path = artifacts_dir / run_id / "episode_timeline.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "duration_ms": 2000,
                "turn_count": 1,
                "turns": [{"index": 0, "speaker": "Ada", "text": "Hello.", "start_ms": 0, "end_ms": 2000}],
            }
        ),
        encoding="utf-8",
    )
    return path


def _finish(client, run_id: str) -> None:
    """Put a run into a terminal state the way a user would: cancel it."""
    assert client.post(f"/runs/{run_id}/cancel").status_code == 200


def test_unknown_run_is_404(client):
    assert client.post("/runs/nope/regenerate-video", json={}).status_code == 404


def test_a_run_still_in_flight_is_rejected(client):
    project = _project(client)
    run = _run(client, project["id"])  # stays queued: the pipeline is stubbed

    res = client.post(f"/runs/{run['id']}/regenerate-video", json={})

    assert res.status_code == 409
    assert "queued" in res.json()["detail"]


def test_a_run_without_a_timeline_cannot_be_recut(client):
    """The timeline is what the compositor cuts against; audio alone is not enough."""
    project = _project(client)
    run = _run(client, project["id"])
    _finish(client, run["id"])

    res = client.post(f"/runs/{run['id']}/regenerate-video", json={})

    assert res.status_code == 400
    assert "no episode timeline" in res.json()["detail"]


def test_recutting_requeues_the_same_run(client, artifacts_dir: Path):
    project = _project(client)
    run = _run(client, project["id"])
    _finish(client, run["id"])
    _write_timeline(artifacts_dir, run["id"])

    res = client.post(f"/runs/{run['id']}/regenerate-video", json={})

    assert res.status_code == 200
    body = res.json()
    assert body["id"] == run["id"]  # same run, not a new one
    assert body["status"] == "queued"
    assert body["error"] is None


def test_look_settings_are_persisted_on_the_run(client, artifacts_dir: Path):
    project = _project(client)
    run = _run(client, project["id"])
    _finish(client, run["id"])
    _write_timeline(artifacts_dir, run["id"])

    body = client.post(
        f"/runs/{run['id']}/regenerate-video",
        json={"captions": False, "show_broll_slots": False},
    ).json()

    assert body["config"]["video_captions"] is False
    assert body["config"]["video_broll_slots"] is False
    # The script settings the run was created with must survive a re-cut.
    assert body["config"]["mode"] == "teaching"


def test_defaults_turn_both_overlays_on(client, artifacts_dir: Path):
    project = _project(client)
    run = _run(client, project["id"])
    _finish(client, run["id"])
    _write_timeline(artifacts_dir, run["id"])

    body = client.post(f"/runs/{run['id']}/regenerate-video", json={}).json()

    assert body["config"]["video_captions"] is True
    assert body["config"]["video_broll_slots"] is True


def test_a_later_recut_replaces_the_previous_look(client, artifacts_dir: Path):
    project = _project(client)
    run = _run(client, project["id"])
    _finish(client, run["id"])
    _write_timeline(artifacts_dir, run["id"])

    client.post(f"/runs/{run['id']}/regenerate-video", json={"captions": False})
    _finish(client, run["id"])

    body = client.post(f"/runs/{run['id']}/regenerate-video", json={"captions": True}).json()

    assert body["config"]["video_captions"] is True


def test_body_may_be_omitted_entirely(client, artifacts_dir: Path):
    project = _project(client)
    run = _run(client, project["id"])
    _finish(client, run["id"])
    _write_timeline(artifacts_dir, run["id"])

    assert client.post(f"/runs/{run['id']}/regenerate-video").status_code == 200


def test_health_reports_whether_a_full_render_is_possible(client):
    """A missing FFmpeg is diagnosable from the UI, not only from a worker log."""
    body = client.get("/health").json()

    assert body["video_engine"] == "slideshow"
    assert isinstance(body["video_ready"], bool)
    assert body["video_detail"]


def test_an_mp4_artifact_is_served_as_video(client, artifacts_dir: Path):
    """The UI streams the episode from this endpoint, so the type has to be right."""
    from sqlmodel import Session

    from apps.api.db import engine
    from apps.api.models import Artifact, Run

    with Session(engine) as session:
        run = Run(project_id="p1", status="completed", stage="completed", config_json={})
        session.add(run)
        session.commit()
        run_id = run.id

    video = artifacts_dir / run_id / "episode.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42")

    with Session(engine) as session:
        artifact = Artifact(run_id=run_id, type="episode_video_mp4", path=str(video), metadata_json={})
        session.add(artifact)
        session.commit()
        artifact_id = artifact.id

    res = client.get(f"/artifacts/{artifact_id}/file")

    assert res.status_code == 200
    assert res.headers["content-type"] == "video/mp4"
