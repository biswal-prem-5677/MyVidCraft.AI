"""API surface of the "regenerate voice only" control."""

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


def _write_dialogue(artifacts_dir: Path, run_id: str) -> Path:
    path = artifacts_dir / run_id / "dialogue_turns.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([{"speaker": "Ada", "text": "Hello.", "pause_before_ms": 0, "pause_after_ms": 400}]),
        encoding="utf-8",
    )
    return path


def _finish(client, run_id: str) -> None:
    """Put a run into a terminal state the way a user would: cancel it."""
    assert client.post(f"/runs/{run_id}/cancel").status_code == 200


def test_unknown_run_is_404(client):
    assert client.post("/runs/nope/regenerate-voice", json={}).status_code == 404


def test_a_run_still_in_flight_is_rejected(client):
    project = _project(client)
    run = _run(client, project["id"])  # stays queued: the pipeline is stubbed

    res = client.post(f"/runs/{run['id']}/regenerate-voice", json={})

    assert res.status_code == 409
    assert "queued" in res.json()["detail"]


def test_a_run_without_a_script_cannot_be_revoiced(client):
    project = _project(client)
    run = _run(client, project["id"])
    _finish(client, run["id"])

    res = client.post(f"/runs/{run['id']}/regenerate-voice", json={})

    assert res.status_code == 400
    assert "no dialogue script" in res.json()["detail"]


def test_revoicing_requeues_the_run(client, artifacts_dir: Path):
    project = _project(client)
    run = _run(client, project["id"])
    _finish(client, run["id"])
    _write_dialogue(artifacts_dir, run["id"])

    res = client.post(f"/runs/{run['id']}/regenerate-voice", json={})

    assert res.status_code == 200
    body = res.json()
    assert body["id"] == run["id"]  # same run, not a new one
    assert body["status"] == "queued"
    assert body["error"] is None


def test_voice_overrides_are_persisted_on_the_run(client, artifacts_dir: Path):
    project = _project(client)
    run = _run(client, project["id"])
    _finish(client, run["id"])
    _write_dialogue(artifacts_dir, run["id"])

    body = client.post(
        f"/runs/{run['id']}/regenerate-voice",
        json={"voice_overrides": {"Ada": "TTS_MS_EN-US_ZIRA_11.0"}},
    ).json()

    assert body["config"]["voice_overrides"] == {"Ada": "TTS_MS_EN-US_ZIRA_11.0"}
    # The script settings the run was created with must survive a re-voice.
    assert body["config"]["mode"] == "teaching"


def test_an_empty_body_clears_previous_overrides(client, artifacts_dir: Path):
    project = _project(client)
    run = _run(client, project["id"])
    _finish(client, run["id"])
    _write_dialogue(artifacts_dir, run["id"])

    client.post(
        f"/runs/{run['id']}/regenerate-voice",
        json={"voice_overrides": {"Ada": "TTS_MS_EN-US_ZIRA_11.0"}},
    )
    _finish(client, run["id"])

    body = client.post(f"/runs/{run['id']}/regenerate-voice", json={}).json()

    assert "voice_overrides" not in body["config"]


def test_body_may_be_omitted_entirely(client, artifacts_dir: Path):
    project = _project(client)
    run = _run(client, project["id"])
    _finish(client, run["id"])
    _write_dialogue(artifacts_dir, run["id"])

    assert client.post(f"/runs/{run['id']}/regenerate-voice").status_code == 200
