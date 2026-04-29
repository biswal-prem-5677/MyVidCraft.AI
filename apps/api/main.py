from __future__ import annotations

from datetime import datetime
from typing import Any

import json
from pathlib import Path

import anyio
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from packages.core.zentcast_core.schemas import (
    ArtifactOut,
    ProjectCreate,
    ProjectOut,
    RunCreate,
    RunOut,
)

from . import artifact_store
from .db import create_db_and_tables, engine, get_session
from .models import Artifact, Project, Run
from .realtime import subscribe_run_events
from .settings import settings


app = FastAPI(title="zentcast-ai API (Phase 1)", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    artifact_store.ensure_data_dirs()
    create_db_and_tables()


@app.post("/projects", response_model=ProjectOut)
def create_project(payload: ProjectCreate, session: Session = Depends(get_session)) -> ProjectOut:
    p = Project(
        name=payload.name,
        niche=payload.niche,
        brand_profile_json=payload.brand_profile,
        defaults_json=payload.defaults,
    )
    session.add(p)
    session.commit()
    session.refresh(p)
    return ProjectOut(
        id=p.id,
        name=p.name,
        niche=p.niche,
        brand_profile=p.brand_profile_json,
        defaults=p.defaults_json,
        created_at=p.created_at,
    )


@app.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(project_id: str, session: Session = Depends(get_session)) -> ProjectOut:
    p = session.get(Project, project_id)
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectOut(
        id=p.id,
        name=p.name,
        niche=p.niche,
        brand_profile=p.brand_profile_json,
        defaults=p.defaults_json,
        created_at=p.created_at,
    )


@app.post("/projects/{project_id}/runs", response_model=RunOut)
def create_run(
    project_id: str,
    payload: RunCreate,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
) -> RunOut:
    p = session.get(Project, project_id)
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")

    run = Run(project_id=project_id, status="queued", stage="queued", progress=0.0, config_json=payload.model_dump())
    session.add(run)
    session.commit()
    session.refresh(run)

    # Kick off async pipeline.
    from workers.orchestrator.tasks import kickoff_phase1, run_phase1_pipeline_local  # local import

    mode = kickoff_phase1(run.id)
    if mode == "local":
        background.add_task(run_phase1_pipeline_local, run.id)

    return _run_out(run)


@app.get("/runs/{run_id}", response_model=RunOut)
def get_run(run_id: str, session: Session = Depends(get_session)) -> RunOut:
    run = session.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return _run_out(run)


@app.get("/runs/{run_id}/artifacts", response_model=list[ArtifactOut])
def list_artifacts(run_id: str, session: Session = Depends(get_session)) -> list[ArtifactOut]:
    stmt = select(Artifact).where(Artifact.run_id == run_id).order_by(Artifact.created_at.asc())
    rows = session.exec(stmt).all()
    return [
        ArtifactOut(
            id=a.id,
            run_id=a.run_id,
            type=a.type,
            path=a.path,
            created_at=a.created_at,
            metadata=a.metadata_json,
        )
        for a in rows
    ]


@app.get("/artifacts/{artifact_id}/json")
def read_artifact_json(artifact_id: str, session: Session = Depends(get_session)) -> Any:
    a = session.get(Artifact, artifact_id)
    if not a:
        raise HTTPException(status_code=404, detail="Artifact not found")

    base = Path(settings.artifacts_dir).resolve()
    target = Path(a.path).resolve()
    if base not in target.parents and base != target:
        raise HTTPException(status_code=400, detail="Artifact path outside artifacts dir")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Artifact file missing on disk")

    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Artifact is not valid JSON: {e}") from e


@app.get("/artifacts/{artifact_id}/file")
def download_artifact_file(artifact_id: str, session: Session = Depends(get_session)):
    a = session.get(Artifact, artifact_id)
    if not a:
        raise HTTPException(status_code=404, detail="Artifact not found")

    base = Path(settings.artifacts_dir).resolve()
    target = Path(a.path).resolve()
    if base not in target.parents and base != target:
        raise HTTPException(status_code=400, detail="Artifact path outside artifacts dir")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Artifact file missing on disk")

    suffix = target.suffix.lower()
    media = {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".json": "application/json",
    }.get(suffix, "application/octet-stream")
    return FileResponse(path=str(target), media_type=media, filename=target.name)


@app.post("/runs/{run_id}/cancel", response_model=RunOut)
def cancel_run(run_id: str, session: Session = Depends(get_session)) -> RunOut:
    run = session.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status in {"completed", "failed", "cancelled"}:
        return _run_out(run)
    run.status = "cancelled"
    run.completed_at = datetime.utcnow()
    run.stage = "cancelled"
    run.progress = 1.0
    session.add(run)
    session.commit()
    session.refresh(run)
    return _run_out(run)


@app.websocket("/ws/runs/{run_id}")
async def ws_run_events(websocket: WebSocket, run_id: str):
    await websocket.accept()
    # Prefer Redis pubsub if available; fall back to DB polling if not.
    try:
        async for event in subscribe_run_events(run_id):
            await websocket.send_json(event)
        return
    except Exception:
        pass

    last_stage = None
    last_progress = None
    while True:
        try:
            with Session(engine) as session:
                run = session.get(Run, run_id)
                if not run:
                    await websocket.send_json({"type": "error", "message": "Run not found"})
                    await websocket.close()
                    return
                if run.stage != last_stage or run.progress != last_progress:
                    last_stage, last_progress = run.stage, run.progress
                    await websocket.send_json(
                        {
                            "type": "run.poll",
                            "run_id": run.id,
                            "status": run.status,
                            "stage": run.stage,
                            "progress": run.progress,
                            "error": run.error,
                        }
                    )
                if run.status in {"completed", "failed", "cancelled"}:
                    await websocket.close()
                    return
        except Exception as e:
            await websocket.send_json({"type": "error", "message": str(e)})
            await websocket.close()
            return
        await anyio.sleep(0.5)


def _run_out(run: Run) -> RunOut:
    return RunOut(
        id=run.id,
        project_id=run.project_id,
        status=run.status,
        stage=run.stage,
        progress=run.progress,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        error=run.error,
        config=run.config_json,
    )

