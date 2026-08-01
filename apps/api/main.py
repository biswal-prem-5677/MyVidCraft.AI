from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anyio
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from packages.core.myvidcraft_core.schemas import (
    ArtifactOut,
    ProjectCreate,
    ProjectOut,
    RunCreate,
    RunOut,
    VideoRerunCreate,
    VoiceOut,
    VoiceRerunCreate,
)

from . import artifact_store
from .db import create_db_and_tables, engine, get_session
from .models import Artifact, Project, Run
from .realtime import subscribe_run_events
from .settings import settings

log = logging.getLogger(__name__)

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Replaces the deprecated @app.on_event("startup") hook.
    artifact_store.ensure_data_dirs()
    create_db_and_tables()
    yield


app = FastAPI(title="MyVidCraft.ai API", version="0.2.0", lifespan=lifespan)

if not settings.cors_allow_credentials:
    log.warning(
        "CORS origin is '*', so credentialed cross-origin requests are disabled. "
        "Set MYVIDCRAFT_CORS_ORIGINS to explicit origins to re-enable them."
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, Any]:
    from workers.media.audio.encode import ffmpeg_available
    from workers.orchestrator import llm
    from workers.orchestrator.tasks import broker_reachable

    # The video renderer reports its own readiness: FFmpeg is optional, and a run
    # without it still produces a storyboard and captions.
    video_ready, video_detail = False, "unavailable"
    try:
        from workers.media.video.registry import get_renderer

        video_ready, video_detail = get_renderer().available()
    except LookupError as e:
        video_detail = str(e)

    return {
        "status": "ok",
        "llm_enabled": llm.is_enabled(),
        "llm_model": settings.llm_model if llm.is_enabled() else None,
        "broker_reachable": broker_reachable(),
        "ffmpeg": ffmpeg_available(),
        "tts_engine": settings.tts_engine,
        "video_engine": settings.video_engine,
        "video_ready": video_ready,
        "video_detail": video_detail,
    }


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
    return _project_out(p)


@app.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(project_id: str, session: Session = Depends(get_session)) -> ProjectOut:
    p = session.get(Project, project_id)
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    return _project_out(p)


@app.post("/projects/{project_id}/runs", response_model=RunOut)
async def create_run(
    project_id: str,
    payload: RunCreate,
    session: Session = Depends(get_session),
) -> RunOut:
    p = session.get(Project, project_id)
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")

    run = Run(
        project_id=project_id,
        status="queued",
        stage="queued",
        progress=0.0,
        config_json=payload.model_dump(mode="json"),
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    from workers.orchestrator.tasks import kickoff, run_pipeline_local

    if kickoff(run.id) == "local":
        _run_detached(run.id, run_pipeline_local)

    return _run_out(run)


def _run_detached(run_id: str, target) -> None:
    """
    Execute a pipeline in a detached daemon thread.

    Deliberately NOT BackgroundTasks: those run after the response is sent but
    still inside the request's task group, so a multi-minute TTS render would
    hold the connection's resources. A detached worker thread also keeps the
    blocking COM/SAPI calls off the event loop entirely.
    """
    import threading

    threading.Thread(target=target, args=(run_id,), name=f"run-{run_id[:8]}", daemon=True).start()


@app.get("/voices", response_model=list[VoiceOut])
def list_voices() -> list[VoiceOut]:
    """Voices the active speech engine offers, for picking a per-speaker override."""
    from workers.media.tts.registry import get_engine

    try:
        engine = get_engine()
    except LookupError as e:
        # Misconfigured engine name: surface it here rather than at the first
        # synthesis, which happens minutes into a run.
        raise HTTPException(status_code=500, detail=str(e)) from e

    return [VoiceOut(id=v.id, name=v.name) for v in engine.list_voices()]


@app.post("/runs/{run_id}/regenerate-voice", response_model=RunOut)
def regenerate_voice(
    run_id: str,
    payload: VoiceRerunCreate | None = None,
    session: Session = Depends(get_session),
) -> RunOut:
    """
    Re-render audio for an existing run, reusing its script.

    This is the "regenerate voice only" control: the strategy, episode plan and
    dialogue artifacts are left exactly as they are, and only the TTS and
    stitch/master stages run again.
    """
    payload = payload or VoiceRerunCreate()

    run = session.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status not in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Run is {run.status}; wait for it to finish or cancel it before re-voicing",
        )
    # Checked directly rather than via `run_dir`, which would create the directory
    # as a side effect of a validation check.
    if not (Path(settings.artifacts_dir) / run.id / "dialogue_turns.json").exists():
        raise HTTPException(status_code=400, detail="Run has no dialogue script to re-voice")

    # Reassign rather than mutate: SQLAlchemy does not track in-place edits to a
    # JSON column, so a mutated dict would never be written back.
    config = dict(run.config_json or {})
    if payload.voice_overrides:
        config["voice_overrides"] = payload.voice_overrides
    else:
        config.pop("voice_overrides", None)  # empty body => back to automatic casting
    run.config_json = config
    run.status = "queued"
    run.stage = "queued"
    run.error = None
    session.add(run)
    session.commit()
    session.refresh(run)

    from workers.orchestrator.tasks import kickoff_voice, run_voice_pipeline_local

    if kickoff_voice(run.id) == "local":
        _run_detached(run.id, run_voice_pipeline_local)

    return _run_out(run)


@app.post("/runs/{run_id}/regenerate-video", response_model=RunOut)
def regenerate_video(
    run_id: str,
    payload: VideoRerunCreate | None = None,
    session: Session = Depends(get_session),
) -> RunOut:
    """
    Re-cut the episode video, reusing its existing audio.

    The cheapest re-render in the pipeline: the script, the per-turn WAVs and the
    mastered episode are all left alone, and only the compositor runs again. This
    is what makes iterating on the look affordable — a voice rerun would pay for
    a full TTS pass to change a caption setting.
    """
    payload = payload or VideoRerunCreate()

    run = session.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status not in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Run is {run.status}; wait for it to finish or cancel it before re-cutting the video",
        )
    # Checked directly rather than via `run_dir`, which would create the directory
    # as a side effect of a validation check.
    if not (Path(settings.artifacts_dir) / run.id / "episode_timeline.json").exists():
        raise HTTPException(status_code=400, detail="Run has no episode timeline to re-cut")

    # Reassign rather than mutate: SQLAlchemy does not track in-place edits to a
    # JSON column, so a mutated dict would never be written back.
    config = dict(run.config_json or {})
    config["video_captions"] = payload.captions
    config["video_broll_slots"] = payload.show_broll_slots
    run.config_json = config
    run.status = "queued"
    run.stage = "queued"
    run.error = None
    session.add(run)
    session.commit()
    session.refresh(run)

    from workers.orchestrator.tasks import kickoff_video, run_video_pipeline_local

    if kickoff_video(run.id) == "local":
        _run_detached(run.id, run_video_pipeline_local)

    return _run_out(run)


@app.get("/runs/{run_id}", response_model=RunOut)
def get_run(run_id: str, session: Session = Depends(get_session)) -> RunOut:
    run = session.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return _run_out(run)


@app.get("/runs", response_model=list[RunOut])
def list_runs(limit: int = 20, session: Session = Depends(get_session)) -> list[RunOut]:
    limit = max(1, min(200, limit))
    stmt = select(Run).order_by(Run.created_at.desc()).limit(limit)
    return [_run_out(r) for r in session.exec(stmt).all()]


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


def _resolve_artifact_path(artifact: Artifact) -> Path:
    """Resolve an artifact path, refusing anything outside the artifacts dir."""
    base = Path(settings.artifacts_dir).resolve()
    target = Path(artifact.path).resolve()
    if target != base and base not in target.parents:
        raise HTTPException(status_code=400, detail="Artifact path outside artifacts dir")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Artifact file missing on disk")
    return target


@app.get("/artifacts/{artifact_id}/json")
def read_artifact_json(artifact_id: str, session: Session = Depends(get_session)) -> Any:
    a = session.get(Artifact, artifact_id)
    if not a:
        raise HTTPException(status_code=404, detail="Artifact not found")
    target = _resolve_artifact_path(a)
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Artifact is not valid JSON: {e}") from e


@app.get("/artifacts/{artifact_id}/file")
def download_artifact_file(artifact_id: str, session: Session = Depends(get_session)):
    a = session.get(Artifact, artifact_id)
    if not a:
        raise HTTPException(status_code=404, detail="Artifact not found")
    target = _resolve_artifact_path(a)

    media = {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".json": "application/json",
        ".mp4": "video/mp4",
        ".srt": "text/plain; charset=utf-8",
    }.get(target.suffix.lower(), "application/octet-stream")
    return FileResponse(path=str(target), media_type=media, filename=target.name)


@app.post("/runs/{run_id}/cancel", response_model=RunOut)
def cancel_run(run_id: str, session: Session = Depends(get_session)) -> RunOut:
    run = session.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status in TERMINAL_STATUSES:
        return _run_out(run)

    # Workers check this status between stages and between turns, so the write
    # is what actually stops the pipeline.
    run.status = "cancelled"
    run.completed_at = _utcnow()
    run.stage = "cancelled"
    session.add(run)
    session.commit()
    session.refresh(run)

    from workers.orchestrator.progress import publish

    publish(run_id, {"type": "run.cancelled"})
    return _run_out(run)


@app.websocket("/ws/runs/{run_id}")
async def ws_run_events(websocket: WebSocket, run_id: str):
    await websocket.accept()
    try:
        if await _stream_from_redis(websocket, run_id):
            return
        await _stream_from_polling(websocket, run_id)
    except WebSocketDisconnect:
        return
    except Exception as e:
        log.debug("ws closed for run %s: %s", run_id, e)
        try:
            await websocket.close()
        except Exception:
            pass


async def _stream_from_redis(websocket: WebSocket, run_id: str) -> bool:
    """
    Try the Redis pubsub stream.

    Returns True if it handled the connection. The previous version wrapped the
    whole `async for` in a try/except, so a mid-stream Redis failure silently fell
    through to polling *after* events had already been sent. Here, once a single
    event is delivered, a later error propagates instead of double-streaming.
    """
    delivered = False
    try:
        async for event in subscribe_run_events(run_id):
            delivered = True
            await websocket.send_json(event)
            if event.get("type") in {"run.completed", "run.failed", "run.cancelled"}:
                await websocket.close()
                return True
        return delivered
    except WebSocketDisconnect:
        raise
    except Exception as e:
        if delivered:
            raise
        log.debug("redis events unavailable for run %s (%s); polling instead", run_id, e)
        return False


async def _stream_from_polling(websocket: WebSocket, run_id: str) -> None:
    last: tuple[str, str, float] | None = None
    while True:
        with Session(engine) as session:
            run = session.get(Run, run_id)
            if not run:
                await websocket.send_json({"type": "error", "message": "Run not found"})
                await websocket.close()
                return
            snapshot = (run.status, run.stage, run.progress)
            payload = {
                "type": "run.poll",
                "run_id": run.id,
                "status": run.status,
                "stage": run.stage,
                "progress": run.progress,
                "error": run.error,
            }
            terminal = run.status in TERMINAL_STATUSES

        if snapshot != last:
            last = snapshot
            await websocket.send_json(payload)
        if terminal:
            await websocket.close()
            return
        await anyio.sleep(0.5)


def _project_out(p: Project) -> ProjectOut:
    return ProjectOut(
        id=p.id,
        name=p.name,
        niche=p.niche,
        brand_profile=p.brand_profile_json,
        defaults=p.defaults_json,
        created_at=p.created_at,
    )


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
