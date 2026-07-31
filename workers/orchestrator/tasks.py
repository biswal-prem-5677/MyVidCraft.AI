from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from celery import chain
from sqlmodel import Session, select

from apps.api.artifact_store import run_dir, write_json
from apps.api.db import engine
from apps.api.models import Artifact, Project, Run
from packages.core.myvidcraft_core.schemas import ArtifactType, DialogueMode, EpisodePlan, RunStatus
from workers.media.audio.encode import ffmpeg_available, wav_to_mp3
from workers.media.audio.stitch import stitch_turns_to_episode
from workers.media.tts.pyttsx3_tts import list_system_voices, text_to_wav
from workers.media.tts.voices import VoiceAssignment, assign_voices, pace_to_rate

from .celery_app import celery_app
from .phase1_brain import generate_strategy
from .phase1_script import generate_dialogue, generate_episode_plan
from .progress import publish

log = logging.getLogger(__name__)

TERMINAL_STATUSES = {RunStatus.completed.value, RunStatus.failed.value, RunStatus.cancelled.value}

# Everything the voice stages produce. A voice-only rerun replaces all of it and
# leaves the strategy/plan/dialogue artifacts untouched.
VOICE_ARTIFACT_TYPES = (
    ArtifactType.turn_audio_wav.value,
    ArtifactType.turn_audio_manifest.value,
    ArtifactType.episode_audio_wav.value,
    ArtifactType.episode_audio_mp3.value,
)


class RunCancelled(Exception):
    """Raised to unwind a run that the user cancelled mid-flight."""


def _utcnow() -> datetime:
    # datetime.utcnow() is deprecated in 3.12+. Stored naive for column compatibility.
    return datetime.now(timezone.utc).replace(tzinfo=None)


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


def _artifacts_root() -> Path:
    from apps.api.settings import settings

    return Path(settings.artifacts_dir).resolve()


def _safe_unlink(path: str) -> bool:
    """Delete an artifact file, refusing anything outside the artifacts dir."""
    root = _artifacts_root()
    try:
        target = Path(path).resolve()
    except OSError:
        return False
    if target != root and root not in target.parents:
        log.warning("refusing to delete artifact outside artifacts dir: %s", path)
        return False
    try:
        target.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as e:
        log.warning("could not delete %s (%s)", path, e)
        return False


def _purge_voice_artifacts(run_id: str) -> int:
    """
    Drop a run's previously rendered audio — DB rows and files.

    Without this a rerun stacks a second set of `turn_audio_wav` rows on top of
    the first, and the UI shows every turn twice with no way to tell which
    recording is current.
    """
    with _session() as s:
        stmt = select(Artifact).where(
            Artifact.run_id == run_id,
            Artifact.type.in_(VOICE_ARTIFACT_TYPES),  # type: ignore[attr-defined]
        )
        rows = list(s.exec(stmt).all())
        for a in rows:
            _safe_unlink(a.path)
            s.delete(a)
        s.commit()
    return len(rows)


def _run_status(run_id: str) -> str | None:
    with _session() as s:
        run = s.get(Run, run_id)
        return run.status if run else None


def is_cancelled(run_id: str) -> bool:
    return _run_status(run_id) == RunStatus.cancelled.value


def _guard(run_id: str) -> None:
    """Abort the current stage if the run was cancelled or has already finished."""
    status = _run_status(run_id)
    if status is None:
        raise RunCancelled(f"Run {run_id} no longer exists")
    if status in TERMINAL_STATUSES:
        raise RunCancelled(f"Run {run_id} is {status}")


def _fail_run(run_id: str, exc: BaseException) -> None:
    # A cancelled run is a user action, not a failure — leave its status alone.
    if _run_status(run_id) == RunStatus.cancelled.value:
        return
    _update_run(
        run_id,
        status=RunStatus.failed.value,
        stage="failed",
        progress=1.0,
        completed_at=_utcnow(),
        error=f"{type(exc).__name__}: {exc}",
    )
    publish(run_id, {"type": "run.failed", "error": str(exc)})


@celery_app.task(name="phase1.mark_running")
def mark_running(run_id: str):
    _guard(run_id)
    _update_run(
        run_id,
        status=RunStatus.running.value,
        stage="starting",
        progress=0.01,
        started_at=_utcnow(),
        error=None,
    )
    publish(run_id, {"type": "run.started"})
    return run_id


@celery_app.task(name="phase1.brain_generate_strategy")
def brain_generate_strategy(run_id: str):
    _guard(run_id)
    publish(run_id, {"type": "stage.started", "stage": "brain_generate_strategy"})
    _update_run(run_id, stage="brain_generate_strategy", progress=0.12)

    with _session() as s:
        run = s.get(Run, run_id)
        project = s.get(Project, run.project_id) if run else None
        if not project:
            raise RuntimeError("Project not found for run")
        niche = project.niche
        brand_profile = dict(project.brand_profile_json or {})

    strategy = generate_strategy(niche=niche, brand_profile=brand_profile)
    path = write_json(run_id, "strategy_pack.json", strategy.model_dump())
    _create_artifact(run_id, ArtifactType.strategy.value, path, metadata={"topics": len(strategy.viral_topic_slate)})

    publish(run_id, {"type": "artifact.created", "artifact_type": "strategy", "path": path})
    publish(run_id, {"type": "stage.completed", "stage": "brain_generate_strategy"})
    _update_run(run_id, progress=0.22)
    return run_id


@celery_app.task(name="phase1.script_generate_dialogue_json")
def script_generate_dialogue_json(run_id: str):
    _guard(run_id)
    publish(run_id, {"type": "stage.started", "stage": "script_generate_dialogue_json"})
    _update_run(run_id, stage="script_generate_dialogue_json", progress=0.35)

    with _session() as s:
        run = s.get(Run, run_id)
        project = s.get(Project, run.project_id) if run else None
        if not project:
            raise RuntimeError("Project not found for run")
        niche = project.niche
        brand_profile = dict(project.brand_profile_json or {})
        mode_raw = (run.config_json or {}).get("mode", DialogueMode.teaching.value)

    mode = mode_raw if isinstance(mode_raw, DialogueMode) else DialogueMode(mode_raw)

    plan = generate_episode_plan(niche=niche, mode=mode, brand_profile=brand_profile)
    plan_path = write_json(run_id, "episode_plan.json", plan.model_dump())
    _create_artifact(run_id, ArtifactType.episode_plan.value, plan_path, metadata={"title": plan.title})
    publish(run_id, {"type": "artifact.created", "artifact_type": "episode_plan", "path": plan_path})

    _guard(run_id)

    # The plan is threaded through so the dialogue follows its beats and uses its
    # character names, instead of the two artifacts describing different episodes.
    turns = generate_dialogue(niche=niche, mode=mode, brand_profile=brand_profile, plan=plan)
    turns_path = write_json(run_id, "dialogue_turns.json", [t.model_dump() for t in turns])
    _create_artifact(run_id, ArtifactType.dialogue.value, turns_path, metadata={"turn_count": len(turns)})
    publish(run_id, {"type": "artifact.created", "artifact_type": "dialogue", "path": turns_path})

    publish(run_id, {"type": "stage.completed", "stage": "script_generate_dialogue_json"})
    _update_run(run_id, progress=0.55)
    return run_id


def _speaker_roles(run_id: str) -> dict[str, str]:
    """Read speaker -> role from the episode plan so voice casting can honour it."""
    plan_path = run_dir(run_id) / "episode_plan.json"
    if not plan_path.exists():
        return {}
    try:
        plan = EpisodePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("could not read episode plan for voice roles (%s)", e)
        return {}
    return {c.name: c.role for c in plan.characters}


def _voice_overrides(run_id: str) -> dict[str, str]:
    """Per-speaker voice ids chosen by the user on a voice-only rerun."""
    with _session() as s:
        run = s.get(Run, run_id)
        raw = (run.config_json or {}).get("voice_overrides") if run else None
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if v}


@celery_app.task(name="phase2.prepare_voice_rerun")
def prepare_voice_rerun(run_id: str):
    """
    Reset a finished run so the voice stages can run again on the same script.

    Deliberately does not call `_guard`: the whole point is to restart a run that
    is already in a terminal state, so the usual "stop if terminal" check would
    reject exactly the case this exists for.
    """
    with _session() as s:
        run = s.get(Run, run_id)
        if not run:
            raise RunCancelled(f"Run {run_id} no longer exists")

    if not (run_dir(run_id) / "dialogue_turns.json").exists():
        raise RuntimeError("Cannot regenerate voice: this run has no dialogue script on disk")

    removed = _purge_voice_artifacts(run_id)
    _update_run(
        run_id,
        status=RunStatus.running.value,
        stage="tts_generate_turn_audio",
        progress=0.55,
        error=None,
        completed_at=None,
    )
    publish(run_id, {"type": "run.started", "reason": "voice_rerun", "replaced_artifacts": removed})
    return run_id


@celery_app.task(name="phase2.tts_generate_turn_audio")
def tts_generate_turn_audio(run_id: str):
    _guard(run_id)
    publish(run_id, {"type": "stage.started", "stage": "tts_generate_turn_audio"})
    _update_run(run_id, stage="tts_generate_turn_audio", progress=0.60)

    turns_path = run_dir(run_id) / "dialogue_turns.json"
    turns = json.loads(turns_path.read_text(encoding="utf-8"))
    if not turns:
        raise RuntimeError("No dialogue turns to synthesise")

    # Give each speaker a distinct system voice — a two-speaker podcast in one
    # voice is the whole premise failing.
    speakers = [t.get("speaker", "Speaker") for t in turns]
    voices = list_system_voices()
    assignments = assign_voices(speakers, voices, roles=_speaker_roles(run_id))

    # A voice-only rerun may pin specific voices; those win over automatic casting.
    by_id = {v.id: v for v in voices}
    for speaker, voice_id in _voice_overrides(run_id).items():
        if speaker not in assignments:
            log.warning("run %s: voice override for unknown speaker %r ignored", run_id, speaker)
            continue
        chosen = by_id.get(voice_id)
        if chosen is None:
            log.warning("run %s: voice %r is not installed; keeping automatic casting", run_id, voice_id)
            continue
        assignments[speaker] = VoiceAssignment(speaker, chosen.id, chosen.name)

    for speaker, a in assignments.items():
        log.info("run %s: %s -> %s", run_id, speaker, a.voice_name)
    publish(
        run_id,
        {
            "type": "voices.assigned",
            "assignments": {s: a.voice_name for s, a in assignments.items()},
        },
    )

    out_paths: list[str] = []
    total = len(turns)
    for i, t in enumerate(turns):
        _guard(run_id)  # cancellation should stop synthesis, not just flip a flag

        speaker = t.get("speaker", "Speaker")
        text = (t.get("text") or "").strip()
        if not text:
            continue

        assignment = assignments.get(speaker)
        safe_speaker = "".join(ch for ch in speaker.lower() if ch.isalnum() or ch in "-_") or "speaker"
        wav_path = run_dir(run_id) / "turns" / f"turn_{i:04d}_{safe_speaker}.wav"

        text_to_wav(
            text=text,
            out_path=str(wav_path),
            rate=pace_to_rate(t.get("pace_wpm")),
            voice_id=assignment.voice_id if assignment else None,
        )
        stored = str(wav_path).replace("\\", "/")
        out_paths.append(stored)

        # Register each turn individually so the UI can preview per-turn audio.
        _create_artifact(
            run_id,
            ArtifactType.turn_audio_wav.value,
            stored,
            metadata={
                "turn_index": i,
                "speaker": speaker,
                "voice": assignment.voice_name if assignment else "system-default",
                "pace_wpm": t.get("pace_wpm"),
            },
        )
        _update_run(run_id, progress=0.60 + 0.25 * ((i + 1) / total))

    if not out_paths:
        raise RuntimeError("TTS produced no audio")

    manifest_path = write_json(
        run_id,
        "turns/manifest.json",
        {
            "turn_wavs": out_paths,
            "voices": {s: {"voice_id": a.voice_id, "voice_name": a.voice_name} for s, a in assignments.items()},
        },
    )
    _create_artifact(
        run_id,
        ArtifactType.turn_audio_manifest.value,
        manifest_path,
        metadata={"turn_count": len(out_paths)},
    )
    publish(run_id, {"type": "artifact.created", "artifact_type": "turn_audio_manifest", "path": manifest_path})

    publish(run_id, {"type": "stage.completed", "stage": "tts_generate_turn_audio"})
    _update_run(run_id, progress=0.86)
    return run_id


@celery_app.task(name="phase2.audio_stitch_master")
def audio_stitch_master(run_id: str):
    _guard(run_id)
    publish(run_id, {"type": "stage.started", "stage": "audio_stitch_master"})
    _update_run(run_id, stage="audio_stitch_master", progress=0.90)

    turns = json.loads((run_dir(run_id) / "dialogue_turns.json").read_text(encoding="utf-8"))
    manifest = json.loads((run_dir(run_id) / "turns" / "manifest.json").read_text(encoding="utf-8"))
    turn_wavs: list[str] = manifest["turn_wavs"]

    # Turns with empty text are skipped during synthesis, so pauses must be taken
    # from the turns that actually produced audio — zipping raw indices would
    # silently misalign every pause after the first skip.
    spoken = [t for t in turns if (t.get("text") or "").strip()]
    if len(spoken) != len(turn_wavs):
        raise RuntimeError(f"turn/audio mismatch: {len(spoken)} spoken turns vs {len(turn_wavs)} wavs")

    pauses_before = [int(t.get("pause_before_ms", 0) or 0) for t in spoken]
    pauses_after = [int(t.get("pause_after_ms", 400) or 0) for t in spoken]

    episode_wav = run_dir(run_id) / "episode.wav"
    stitch_turns_to_episode(turn_wavs, pauses_before, pauses_after, str(episode_wav), normalize=True)
    wav_path = str(episode_wav).replace("\\", "/")
    _create_artifact(
        run_id,
        ArtifactType.episode_audio_wav.value,
        wav_path,
        metadata={"turn_count": len(turn_wavs), "normalized": True},
    )
    publish(run_id, {"type": "artifact.created", "artifact_type": "episode_audio_wav", "path": wav_path})

    # MP3 is best-effort: FFmpeg may not be installed, and that must not fail the run.
    episode_mp3 = run_dir(run_id) / "episode.mp3"
    if wav_to_mp3(str(episode_wav), str(episode_mp3)):
        mp3_path = str(episode_mp3).replace("\\", "/")
        _create_artifact(run_id, ArtifactType.episode_audio_mp3.value, mp3_path)
        publish(run_id, {"type": "artifact.created", "artifact_type": "episode_audio_mp3", "path": mp3_path})
    elif not ffmpeg_available():
        publish(run_id, {"type": "log", "message": "ffmpeg not found on PATH; skipped mp3 export"})

    publish(run_id, {"type": "stage.completed", "stage": "audio_stitch_master"})
    _update_run(run_id, progress=0.98)
    return run_id


@celery_app.task(name="phase1.mark_completed")
def mark_completed(run_id: str):
    # Do not resurrect a run the user cancelled.
    if _run_status(run_id) == RunStatus.cancelled.value:
        return run_id
    _update_run(
        run_id,
        status=RunStatus.completed.value,
        stage="completed",
        progress=1.0,
        completed_at=_utcnow(),
    )
    publish(run_id, {"type": "run.completed"})
    return run_id


@celery_app.task(name="phase1.mark_failed")
def mark_failed(request, exc, traceback, run_id: str):
    """
    Celery errback.

    Registered via `link_error=mark_failed.s(run_id)`: Celery prepends
    (request, exc, traceback) to the signature's existing args, so `run_id`
    lands last. The previous `.s(run_id)` on a 3-positional-arg task bound
    run_id to `request` and then called it with one more arg than it accepts.
    """
    if isinstance(exc, RunCancelled):
        return
    _fail_run(run_id, exc)


@celery_app.task(name="phase1.start_pipeline")
def start_pipeline(run_id: str):
    """Schedule the task graph and return immediately."""
    chain(
        mark_running.s(run_id),
        brain_generate_strategy.s(),
        script_generate_dialogue_json.s(),
        tts_generate_turn_audio.s(),
        audio_stitch_master.s(),
        mark_completed.s(),
    ).apply_async(link_error=mark_failed.s(run_id))
    return run_id


# Kept for backwards compatibility with the previous task name.
start_phase1_pipeline = start_pipeline


@celery_app.task(name="phase2.start_voice_pipeline")
def start_voice_pipeline(run_id: str):
    """Re-render audio for an existing run without touching its script."""
    chain(
        prepare_voice_rerun.s(run_id),
        tts_generate_turn_audio.s(),
        audio_stitch_master.s(),
        mark_completed.s(),
    ).apply_async(link_error=mark_failed.s(run_id))
    return run_id


def _run_stages_local(run_id: str, stages) -> str:
    """
    Run stages synchronously in-process.

    Used when no Celery broker is reachable. Cancellation surfaces as
    RunCancelled and is not treated as a failure.
    """
    try:
        for stage in stages:
            stage(run_id)
    except RunCancelled as e:
        log.info("run %s stopped: %s", run_id, e)
        publish(run_id, {"type": "run.cancelled"})
    except Exception as e:
        log.exception("run %s failed", run_id)
        _fail_run(run_id, e)
    return run_id


def run_pipeline_local(run_id: str) -> str:
    """In-process fallback for the full pipeline."""
    return _run_stages_local(
        run_id,
        (
            mark_running,
            brain_generate_strategy,
            script_generate_dialogue_json,
            tts_generate_turn_audio,
            audio_stitch_master,
            mark_completed,
        ),
    )


def run_voice_pipeline_local(run_id: str) -> str:
    """In-process fallback for a voice-only rerun."""
    return _run_stages_local(
        run_id,
        (
            prepare_voice_rerun,
            tts_generate_turn_audio,
            audio_stitch_master,
            mark_completed,
        ),
    )


run_phase1_pipeline_local = run_pipeline_local


def broker_reachable(timeout: float = 0.25) -> bool:
    """
    Cheap TCP probe of the Celery broker.

    `.delay()` on an unreachable broker blocks for the full kombu retry budget —
    that is what made starting a run take ~110 seconds with Redis down, against
    an MVP target of under 2 seconds. Probing first keeps the failure instant.
    """
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(settings_redis_url())
    host = parsed.hostname or "localhost"
    port = parsed.port or 6379
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def settings_redis_url() -> str:
    from apps.api.settings import settings

    return settings.redis_url


def kickoff(run_id: str) -> str:
    """
    Start a run.

    Returns "celery" if the job was enqueued on a worker, or "local" if the caller
    should execute it in-process.
    """
    return _kickoff(run_id, start_pipeline)


def kickoff_voice(run_id: str) -> str:
    """Start a voice-only rerun. Same contract as `kickoff`."""
    return _kickoff(run_id, start_voice_pipeline)


def _kickoff(run_id: str, task) -> str:
    if not broker_reachable():
        return "local"
    try:
        task.delay(run_id)
        return "celery"
    except Exception as e:
        log.warning("celery enqueue failed (%s); running locally", e)
        return "local"


kickoff_phase1 = kickoff
