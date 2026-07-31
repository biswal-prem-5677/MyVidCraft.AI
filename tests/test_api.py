import json
from pathlib import Path


def _project(client, name="Test project", niche="AI productivity"):
    res = client.post(
        "/projects",
        json={"name": name, "niche": niche, "brand_profile": {"tone": "confident"}, "defaults": {}},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _run(client, project_id, mode="teaching"):
    res = client.post(
        f"/projects/{project_id}/runs",
        json={"mode": mode, "platforms": ["youtube", "podcast"], "language": "en", "config": {}},
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_health_reports_capabilities(client):
    body = client.get("/health").json()

    assert body["status"] == "ok"
    assert set(body) >= {"llm_enabled", "broker_reachable", "ffmpeg"}


def test_project_round_trip(client):
    created = _project(client)
    fetched = client.get(f"/projects/{created['id']}").json()

    assert fetched["niche"] == "AI productivity"
    assert fetched["brand_profile"] == {"tone": "confident"}


def test_unknown_project_is_404(client):
    assert client.get("/projects/does-not-exist").status_code == 404


def test_run_starts_queued_and_is_listed(client):
    project = _project(client)
    run = _run(client, project["id"])

    assert run["status"] == "queued"
    assert run["config"]["mode"] == "teaching"

    listed = client.get("/runs?limit=50").json()
    assert run["id"] in [r["id"] for r in listed]

    assert client.get(f"/runs/{run['id']}/artifacts").json() == []


def test_run_cannot_be_created_for_a_missing_project(client):
    res = client.post(
        "/projects/nope/runs",
        json={"mode": "teaching", "platforms": ["youtube"], "language": "en", "config": {}},
    )
    assert res.status_code == 404


def test_cancel_is_idempotent(client):
    project = _project(client)
    run = _run(client, project["id"])

    first = client.post(f"/runs/{run['id']}/cancel").json()
    assert first["status"] == "cancelled"

    second = client.post(f"/runs/{run['id']}/cancel").json()
    assert second["status"] == "cancelled"
    assert second["completed_at"] == first["completed_at"]


def test_unknown_artifact_is_404(client):
    assert client.get("/artifacts/nope/json").status_code == 404
    assert client.get("/artifacts/nope/file").status_code == 404


def test_artifact_outside_the_artifacts_dir_is_refused(client, tmp_path):
    from sqlmodel import Session

    from apps.api.db import engine
    from apps.api.models import Artifact

    escapee = tmp_path / "secret.json"
    escapee.write_text('{"secret": true}', encoding="utf-8")

    with Session(engine) as session:
        row = Artifact(run_id="run-x", type="dialogue", path=str(escapee), metadata_json={})
        session.add(row)
        session.commit()
        artifact_id = row.id

    assert client.get(f"/artifacts/{artifact_id}/json").status_code == 400
    assert client.get(f"/artifacts/{artifact_id}/file").status_code == 400


def test_artifact_json_is_served_from_disk(client, artifacts_dir: Path):
    from sqlmodel import Session

    from apps.api.db import engine
    from apps.api.models import Artifact

    target = artifacts_dir / "run-json" / "dialogue_turns.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = [{"speaker": "Ada", "text": "Hello."}]
    target.write_text(json.dumps(payload), encoding="utf-8")

    with Session(engine) as session:
        row = Artifact(run_id="run-json", type="dialogue", path=str(target), metadata_json={})
        session.add(row)
        session.commit()
        artifact_id = row.id

    assert client.get(f"/artifacts/{artifact_id}/json").json() == payload


def test_voices_endpoint_returns_a_list(client):
    body = client.get("/voices").json()

    assert isinstance(body, list)
    for v in body:
        assert set(v) == {"id", "name"}
