from __future__ import annotations

import json
from datetime import datetime

from celery import chain
from celery.exceptions import CeleryError
from sqlmodel import Session

from apps.api.artifact_store import write_json
from apps.api.db import engine
from apps.api.models import Artifact, Project, Run
from packages.core.zentcast_core.schemas import ArtifactType, DialogueMode, RunStatus

from .celery_app import celery_app
from .phase1_brain import generate_strategy
from .phase1_script import generate_dialogue, generate_episode_plan
from .progress import publish


def _session() -> Session:
    return Session(engine)


def _update_run(run_id: str, **updates):
    with _session() as s:
        run = s.get(Run, run_id)
        if not run:
            return
        for k, v in updates.items():
            setattr(run, k, v)
        s.add(run)
        s.commit()


def _create_artifact(run_id: str, type_: str, path: str, metadata: dict | None = None):
    metadata = metadata or {}
    with _session() as s:
        a = Artifact(run_id=run_id, type=type_, path=path, metadata_json=metadata)
        s.add(a)
        s.commit()


@celery_app.task(name="phase1.mark_running")
def mark_running(run_id: str):
    _update_run(
        run_id,
        status=RunStatus.running.value,
        stage="starting",
        progress=0.01,
        started_at=datetime.utcnow(),
        error=None,
    )
    publish(run_id, {"type": "run.started"})
    return run_id


@celery_app.task(name="phase1.brain_generate_strategy")
def brain_generate_strategy(run_id: str):
    publish(run_id, {"type": "stage.started", "stage": "brain_generate_strategy"})
    _update_run(run_id, stage="brain_generate_strategy", progress=0.12)

    with _session() as s:
        run = s.get(Run, run_id)
        if not run or run.status == RunStatus.cancelled.value:
            return run_id
        project = s.get(Project, run.project_id)
        if not project:
            raise RuntimeError("Project not found for run")
        niche = project.niche
        brand_profile = project.brand_profile_json

    strategy = generate_strategy(niche=niche, brand_profile=brand_profile)
    path = write_json(run_id, "strategy_pack.json", strategy.model_dump())
    _create_artifact(run_id, ArtifactType.strategy.value, path)

    publish(run_id, {"type": "artifact.created", "artifact_type": "strategy", "path": path})
    publish(run_id, {"type": "stage.completed", "stage": "brain_generate_strategy"})
    _update_run(run_id, progress=0.22)
    return run_id


@celery_app.task(name="phase1.script_generate_dialogue_json")
def script_generate_dialogue_json(run_id: str):
    publish(run_id, {"type": "stage.started", "stage": "script_generate_dialogue_json"})
    _update_run(run_id, stage="script_generate_dialogue_json", progress=0.35)

    with _session() as s:
        run = s.get(Run, run_id)
        if not run or run.status == RunStatus.cancelled.value:
            return run_id
        project = s.get(Project, run.project_id)
        if not project:
            raise RuntimeError("Project not found for run")
        niche = project.niche
        brand_profile = project.brand_profile_json
        mode_raw = run.config_json.get("mode", "teaching")
        mode = DialogueMode(mode_raw) if not isinstance(mode_raw, DialogueMode) else mode

    plan = generate_episode_plan(niche=niche, mode=mode, brand_profile=brand_profile)
    plan_path = write_json(run_id, "episode_plan.json", plan.model_dump())
    _create_artifact(run_id, ArtifactType.episode_plan.value, plan_path)
    publish(run_id, {"type": "artifact.created", "artifact_type": "episode_plan", "path": plan_path})

    turns = generate_dialogue(niche=niche, mode=mode, brand_profile=brand_profile)
    turns_path = write_json(run_id, "dialogue_turns.json", [t.model_dump() for t in turns])
    _create_artifact(run_id, ArtifactType.dialogue.value, turns_path, metadata={"turn_count": len(turns)})
    publish(run_id, {"type": "artifact.created", "artifact_type": "dialogue", "path": turns_path})

    publish(run_id, {"type": "stage.completed", "stage": "script_generate_dialogue_json"})
    _update_run(run_id, progress=0.75)
    return run_id


@celery_app.task(name="phase1.mark_completed")
def mark_completed(run_id: str):
    _update_run(
        run_id,
        status=RunStatus.completed.value,
        stage="completed",
        progress=1.0,
        completed_at=datetime.utcnow(),
    )
    publish(run_id, {"type": "run.completed"})
    return run_id


@celery_app.task(name="phase1.mark_failed")
def mark_failed(request, exc, traceback, run_id: str):
    _update_run(
        run_id,
        status=RunStatus.failed.value,
        stage="failed",
        progress=1.0,
        completed_at=datetime.utcnow(),
        error=str(exc),
    )
    publish(run_id, {"type": "run.failed", "error": str(exc)})


@celery_app.task(name="phase1.start_pipeline")
def start_phase1_pipeline(run_id: str):
    """
    Schedules the Phase 1 task graph and returns immediately.
    """
    chain(
        mark_running.s(run_id),
        brain_generate_strategy.s(),
        script_generate_dialogue_json.s(),
        mark_completed.s(),
    ).apply_async(link_error=mark_failed.s(run_id))
    return run_id


def run_phase1_pipeline_local(run_id: str) -> str:
    """
    Local dev fallback (no Redis/Celery worker needed).
    Runs the same stages synchronously and updates DB + best-effort progress events.
    """
    try:
        mark_running(run_id)
        brain_generate_strategy(run_id)
        script_generate_dialogue_json(run_id)
        mark_completed(run_id)
    except Exception as e:
        _update_run(
            run_id,
            status=RunStatus.failed.value,
            stage="failed",
            progress=1.0,
            completed_at=datetime.utcnow(),
            error=str(e),
        )
        publish(run_id, {"type": "run.failed", "error": str(e)})
    return run_id


def kickoff_phase1(run_id: str) -> str:
    """
    Best-effort kickoff:
    - If Celery broker is reachable, enqueue `start_phase1_pipeline`.
    - Otherwise fall back to local background execution (handled by API).
    """
    try:
        start_phase1_pipeline.delay(run_id)
        return "celery"
    except Exception:
        return "local"

