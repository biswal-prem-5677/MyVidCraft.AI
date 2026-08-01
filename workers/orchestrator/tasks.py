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
from packages.core.myvidcraft_core.stages import STARTING_PROGRESS, Stage, progress_at, stage_bounds
from workers.media.audio.encode import ffmpeg_available, wav_to_mp3
from workers.media.audio.stitch import stitch_turns_to_episode
from workers.media.tts.registry import get_engine
from workers.media.tts.voices import VoiceAssignment, assign_voices, pace_to_rate
from workers.media.video.base import EpisodeVideoSpec
from workers.media.video.captions import build_captions, to_srt
from workers.media.video.registry import get_renderer
from workers.media.video.slideshow import build_speaker_styles

from .brain import generate_strategy
from .celery_app import celery_app
from .dialogue import generate_dialogue, generate_episode_plan
from .progress import publish

log = logging.getLogger(__name__)

TERMINAL_STATUSES = {RunStatus.completed.value, RunStatus.failed.value, RunStatus.cancelled.value}

# Everything the video stage produces.
VIDEO_ARTIFACT_TYPES = (
    ArtifactType.episode_video_mp4.value,
    ArtifactType.episode_captions_srt.value,
    ArtifactType.video_storyboard.value,
)

# Everything the voice stages produce. A voice-only rerun replaces all of it and
# leaves the strategy/plan/dialogue artifacts untouched. The video artifacts are
# included because they were cut against the old audio: keeping them would leave
# an episode.mp4 whose captions and cuts no longer match the WAV beside it.
VOICE_ARTIFACT_TYPES = (
    ArtifactType.turn_audio_wav.value,
    ArtifactType.turn_audio_manifest.value,
    ArtifactType.episode_audio_wav.value,
    ArtifactType.episode_audio_mp3.value,
    ArtifactType.episode_timeline.value,
    *VIDEO_ARTIFACT_TYPES,
)


class RunCancelled(Exception):
    """Raised to unwind a run that the user cancelled mid-flight."""


# Retry only what a second attempt could plausibly fix.
#
# In practice that means filesystem I/O. Every stage writes artifacts through
# `write_json`/`wave`/the TTS engine, and those are the calls that fail for
# reasons a retry clears: a disk that is momentarily full, a virus scanner
# holding a freshly written WAV open (PermissionError on Windows), a path that
# is briefly unavailable. All surface as OSError.
#
# Worth being precise about what this does NOT cover, because the obvious
# guesses are wrong:
#   - SQLite lock contention raises sqlalchemy.exc.OperationalError, which is
#     not an OSError. It is not retried here.
#   - FFmpeg failures never reach us: `wav_to_mp3` and `mux_episode` catch their
#     own OSError and return False, by design — a missing codec must not fail a
#     run.
#   - A SAPI/COM fault raises pywintypes.com_error, which derives straight from
#     Exception. `text_to_wav` is where a retry for that would have to live.
#
# Deliberately NOT `Exception`: a schema violation fails identically on every
# attempt, so retrying turns an instant, legible failure into a slow one.
# RunCancelled is not an OSError either, so cancelling a run unwinds immediately
# instead of being retried first.
#
# FileNotFoundError is an OSError and so is technically in scope, which means a
# genuinely absent input costs two pointless attempts (~6s) before it reports.
# That is accepted rather than special-cased: the bound is small, and carving
# exceptions out of OSError would also drop the transient cases above.
#
# Safe because artifact registration is idempotent — see `_create_artifact`.
RETRYABLE_ERRORS = (OSError,)

TASK_RETRY_POLICY = {
    "autoretry_for": RETRYABLE_ERRORS,
    "retry_backoff": 2,        # 2s, then 4s
    "retry_backoff_max": 30,
    "retry_jitter": True,      # spread retries when several turns fail at once
    "max_retries": 2,
}


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
    """
    Register an artifact, replacing any previous row describing the same file.

    Registration has to be idempotent because a stage can run more than once for
    one run — a retry after a transient failure, or a rerun. Inserting blindly
    left two rows pointing at one file, and the UI then showed the artifact twice
    with no way to tell which row was current. Keying on (run, type, path) makes
    re-running a stage converge instead of accumulate.

    Per-turn WAVs are unaffected: each turn writes its own path, so they are
    distinct rows rather than replacements of one another.
    """
    metadata = metadata or {}
    with _session() as s:
        stmt = select(Artifact).where(
            Artifact.run_id == run_id,
            Artifact.type == type_,
            Artifact.path == path,
        )
        existing = s.exec(stmt).first()
        if existing is not None:
            existing.metadata_json = metadata
            existing.created_at = _utcnow()
            s.add(existing)
        else:
            s.add(Artifact(run_id=run_id, type=type_, path=path, metadata_json=metadata))
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


def _purge_artifacts(run_id: str, types: tuple[str, ...]) -> int:
    """
    Drop previously rendered artifacts of `types` — DB rows and files.

    Without this a rerun stacks a second set of `turn_audio_wav` rows on top of
    the first, and the UI shows every turn twice with no way to tell which
    recording is current.
    """
    with _session() as s:
        stmt = select(Artifact).where(
            Artifact.run_id == run_id,
            Artifact.type.in_(types),  # type: ignore[attr-defined]
        )
        rows = list(s.exec(stmt).all())
        for a in rows:
            _safe_unlink(a.path)
            s.delete(a)
        s.commit()
    return len(rows)


def _purge_voice_artifacts(run_id: str) -> int:
    return _purge_artifacts(run_id, VOICE_ARTIFACT_TYPES)


def _purge_video_artifacts(run_id: str) -> int:
    return _purge_artifacts(run_id, VIDEO_ARTIFACT_TYPES)


def _clear_frames_dir(run_id: str) -> None:
    """
    Remove the previous render's frames.

    The compositor writes `scene_0000.ppm`, `scene_0001.ppm`, ... and a re-render
    with fewer scenes would otherwise leave the tail of the old one on disk —
    harmless to playback, since the playlist names only current frames, but the
    frames are the largest thing a run writes and they would accumulate on every
    re-render.
    """
    frames = run_dir(run_id) / "frames"
    if not frames.is_dir():
        return
    root = _artifacts_root()
    if root not in frames.resolve().parents:
        log.warning("refusing to clear frames dir outside artifacts dir: %s", frames)
        return
    for child in frames.iterdir():
        if child.is_file():
            _safe_unlink(str(child))


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
        stage=Stage.failed.value,
        progress=1.0,
        completed_at=_utcnow(),
        error=f"{type(exc).__name__}: {exc}",
    )
    publish(run_id, {"type": "run.failed", "error": str(exc)})


def _begin_stage(run_id: str, stage: Stage) -> None:
    """
    Enter a stage: announce it and move the bar to the stage's opening position.

    Paired with `_end_stage` so the published stage and the stored stage cannot
    drift apart, which is what happened when each task spelled its own name twice.
    """
    publish(run_id, {"type": "stage.started", "stage": stage.value})
    start, _ = stage_bounds(stage)
    _update_run(run_id, stage=stage.value, progress=start)


def _end_stage(run_id: str, stage: Stage) -> None:
    publish(run_id, {"type": "stage.completed", "stage": stage.value})
    _update_run(run_id, progress=progress_at(stage))


def _report_progress(run_id: str, stage: Stage, fraction: float) -> None:
    """
    Advance the bar inside a long stage, and say so on the event stream.

    The two long stages used to move the bar with a bare `_update_run`, so a
    client watching the websocket saw nothing at all between `stage.started` and
    `stage.completed` — on the voice stage that is the multi-minute stretch where
    a user most wants to know something is happening. `progress_at` clamps, so a
    stage that miscounts its own items cannot push the bar into the next one.
    """
    value = progress_at(stage, fraction)
    _update_run(run_id, progress=value)
    publish(
        run_id,
        {
            "type": "stage.progress",
            "stage": stage.value,
            "progress": value,
            "fraction": round(max(0.0, min(1.0, fraction)), 4),
        },
    )


@celery_app.task(name="run.mark_running")
def mark_running(run_id: str):
    _guard(run_id)
    _update_run(
        run_id,
        status=RunStatus.running.value,
        stage=Stage.starting.value,
        progress=STARTING_PROGRESS,
        started_at=_utcnow(),
        error=None,
    )
    publish(run_id, {"type": "run.started"})
    return run_id


@celery_app.task(name="brain.generate_strategy", **TASK_RETRY_POLICY)
def brain_generate_strategy(run_id: str):
    _guard(run_id)
    _begin_stage(run_id, Stage.brain_strategy)

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
    _end_stage(run_id, Stage.brain_strategy)
    return run_id


@celery_app.task(name="dialogue.generate_script", **TASK_RETRY_POLICY)
def dialogue_generate_script(run_id: str):
    _guard(run_id)
    _begin_stage(run_id, Stage.dialogue_script)

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

    _end_stage(run_id, Stage.dialogue_script)
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


@celery_app.task(name="voice.prepare_rerun")
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
        stage=Stage.voice_turns.value,
        progress=stage_bounds(Stage.voice_turns)[0],
        error=None,
        completed_at=None,
    )
    publish(run_id, {"type": "run.started", "reason": "voice_rerun", "replaced_artifacts": removed})
    return run_id


@celery_app.task(name="voice.generate_turn_audio", **TASK_RETRY_POLICY)
def voice_generate_turn_audio(run_id: str):
    _guard(run_id)
    _begin_stage(run_id, Stage.voice_turns)

    turns_path = run_dir(run_id) / "dialogue_turns.json"
    turns = json.loads(turns_path.read_text(encoding="utf-8"))
    if not turns:
        raise RuntimeError("No dialogue turns to synthesise")

    speech = get_engine()

    # Give each speaker a distinct voice — a two-speaker podcast in one voice is
    # the whole premise failing.
    speakers = [t.get("speaker", "Speaker") for t in turns]
    voices = speech.list_voices()
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
            "engine": speech.name,
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

        speech.synthesize(
            text,
            str(wav_path),
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
                "engine": speech.name,
                "pace_wpm": t.get("pace_wpm"),
            },
        )
        _report_progress(run_id, Stage.voice_turns, (i + 1) / total)

    if not out_paths:
        raise RuntimeError("TTS produced no audio")

    manifest_path = write_json(
        run_id,
        "turns/manifest.json",
        {
            "engine": speech.name,
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

    _end_stage(run_id, Stage.voice_turns)
    return run_id


@celery_app.task(name="audio.stitch_master", **TASK_RETRY_POLICY)
def audio_stitch_master(run_id: str):
    _guard(run_id)
    _begin_stage(run_id, Stage.audio_master)

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
    timings = stitch_turns_to_episode(turn_wavs, pauses_before, pauses_after, str(episode_wav), normalize=True)
    wav_path = str(episode_wav).replace("\\", "/")
    _create_artifact(
        run_id,
        ArtifactType.episode_audio_wav.value,
        wav_path,
        metadata={"turn_count": len(turn_wavs), "normalized": True},
    )
    publish(run_id, {"type": "artifact.created", "artifact_type": "episode_audio_wav", "path": wav_path})

    _write_timeline(run_id, spoken, timings, episode_wav)

    # MP3 is best-effort: FFmpeg may not be installed, and that must not fail the run.
    episode_mp3 = run_dir(run_id) / "episode.mp3"
    if wav_to_mp3(str(episode_wav), str(episode_mp3)):
        mp3_path = str(episode_mp3).replace("\\", "/")
        _create_artifact(run_id, ArtifactType.episode_audio_mp3.value, mp3_path)
        publish(run_id, {"type": "artifact.created", "artifact_type": "episode_audio_mp3", "path": mp3_path})
    elif not ffmpeg_available():
        publish(run_id, {"type": "log", "message": "ffmpeg not found on PATH; skipped mp3 export"})

    _end_stage(run_id, Stage.audio_master)
    return run_id


def _episode_duration_ms(wav_path: Path) -> int:
    """Read the mastered episode's true length, trailing pause included."""
    import wave

    try:
        with wave.open(str(wav_path), "rb") as w:
            return int(round(w.getnframes() * 1000.0 / w.getframerate()))
    except (OSError, wave.Error) as e:
        log.warning("could not measure episode duration (%s)", e)
        return 0


def _write_timeline(run_id: str, spoken: list[dict], timings, episode_wav: Path) -> str:
    """
    Record where every turn landed in the mastered episode.

    This is the hand-off between the audio and video engines. The stitcher is the
    only thing that knows the real frame offsets, so it publishes them here rather
    than letting the compositor re-derive timings from pause values and drift out
    of sync with the file it is cutting. Phase 4's clip engine reads the same
    artifact.
    """
    turns = [
        {
            "index": t.index,
            "speaker": spoken[t.index].get("speaker", "Speaker"),
            "text": spoken[t.index].get("text", ""),
            "emotion": spoken[t.index].get("emotion", "neutral"),
            "broll_cues": spoken[t.index].get("broll_cues") or [],
            "start_ms": t.start_ms,
            "end_ms": t.end_ms,
        }
        for t in timings
    ]
    payload = {
        "duration_ms": _episode_duration_ms(episode_wav),
        "turn_count": len(turns),
        "turns": turns,
    }
    path = write_json(run_id, "episode_timeline.json", payload)
    _create_artifact(
        run_id,
        ArtifactType.episode_timeline.value,
        path,
        metadata={"turn_count": len(turns), "duration_ms": payload["duration_ms"]},
    )
    publish(run_id, {"type": "artifact.created", "artifact_type": "episode_timeline", "path": path})
    return path


def _episode_title(run_id: str) -> str:
    plan_path = run_dir(run_id) / "episode_plan.json"
    if not plan_path.exists():
        return "Episode"
    try:
        return EpisodePlan.model_validate_json(plan_path.read_text(encoding="utf-8")).title
    except Exception:
        return "Episode"


@celery_app.task(name="video.prepare_rerun")
def prepare_video_rerun(run_id: str):
    """
    Reset a finished run so the compositor can run again on the same audio.

    Like `prepare_voice_rerun`, this deliberately skips `_guard`: it exists to
    restart a run that is already terminal.
    """
    with _session() as s:
        if not s.get(Run, run_id):
            raise RunCancelled(f"Run {run_id} no longer exists")

    if not (run_dir(run_id) / "episode_timeline.json").exists():
        raise RuntimeError("Cannot regenerate video: this run has no episode timeline on disk")

    removed = _purge_video_artifacts(run_id)
    _clear_frames_dir(run_id)
    _update_run(
        run_id,
        status=RunStatus.running.value,
        stage=Stage.video_compose.value,
        progress=stage_bounds(Stage.video_compose)[0],
        error=None,
        completed_at=None,
    )
    publish(run_id, {"type": "run.started", "reason": "video_rerun", "replaced_artifacts": removed})
    return run_id


@celery_app.task(name="video.compose_episode", **TASK_RETRY_POLICY)
def video_compose_episode(run_id: str):
    _guard(run_id)
    _begin_stage(run_id, Stage.video_compose)

    timeline_path = run_dir(run_id) / "episode_timeline.json"
    if not timeline_path.exists():
        raise RuntimeError("No episode timeline to compose from")
    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    turns = timeline.get("turns") or []
    if not turns:
        raise RuntimeError("Episode timeline contains no turns")

    episode_wav = run_dir(run_id) / "episode.wav"
    if not episode_wav.exists():
        raise RuntimeError("No mastered episode audio to compose against")

    with _session() as s:
        run = s.get(Run, run_id)
        config = dict(run.config_json or {}) if run else {}
    show_captions = bool(config.get("video_captions", True))
    show_broll = bool(config.get("video_broll_slots", True))

    renderer = get_renderer()
    ok, reason = renderer.available()
    if not ok:
        # Not fatal: the storyboard and captions are still produced. The reason
        # reaches the UI so a missing FFmpeg is diagnosable without a worker log.
        publish(run_id, {"type": "log", "message": f"video: {reason}"})

    captions = build_captions(turns) if show_captions else []
    if captions:
        srt_path = run_dir(run_id) / "captions.srt"
        srt_path.write_text(to_srt(captions), encoding="utf-8")
        stored = str(srt_path).replace("\\", "/")
        _create_artifact(
            run_id,
            ArtifactType.episode_captions_srt.value,
            stored,
            metadata={"caption_count": len(captions), "source": "turn_timeline"},
        )
        publish(run_id, {"type": "artifact.created", "artifact_type": "episode_captions_srt", "path": stored})

    _report_progress(run_id, Stage.video_compose, 0.3)
    _guard(run_id)

    from apps.api.settings import settings

    spec = EpisodeVideoSpec(
        run_id=run_id,
        title=_episode_title(run_id),
        audio_path=str(episode_wav),
        turns=turns,
        captions=captions,
        speakers=build_speaker_styles(turns, roles=_speaker_roles(run_id)),
        out_dir=str(run_dir(run_id)),
        width=settings.video_width,
        height=settings.video_height,
        fps=settings.video_fps,
        duration_ms=int(timeline.get("duration_ms") or 0),
        show_captions=show_captions,
        show_broll_slots=show_broll,
    )

    result = renderer.render(spec)

    # The frames are uncompressed PPM — ~2.7 MB each at 720p, so a one-minute
    # episode writes well over 100 MB of them. Once the mux has succeeded they are
    # redundant intermediates and the mp4 is the deliverable, so they go. Without
    # an mp4 they *are* the output and must stay, which is the whole reason a run
    # without FFmpeg still completes.
    frames_kept = result.video_path is None
    if not frames_kept:
        _clear_frames_dir(run_id)

    scenes = result.scenes
    if not frames_kept:
        # Strip the frame paths rather than leave the storyboard pointing at files
        # that were just deleted.
        scenes = [{k: v for k, v in scene.items() if k != "frame"} for scene in scenes]

    storyboard_path = write_json(
        run_id,
        "storyboard.json",
        {
            "engine": renderer.name,
            "width": spec.width,
            "height": spec.height,
            "fps": spec.fps,
            "duration_ms": spec.duration_ms,
            "video": result.video_path,
            "notes": result.notes,
            "frames_kept": frames_kept,
            "scenes": scenes,
        },
    )
    _create_artifact(
        run_id,
        ArtifactType.video_storyboard.value,
        storyboard_path,
        metadata={"scene_count": len(scenes), "engine": renderer.name, "frames_kept": frames_kept},
    )
    publish(run_id, {"type": "artifact.created", "artifact_type": "video_storyboard", "path": storyboard_path})

    if result.video_path:
        _create_artifact(
            run_id,
            ArtifactType.episode_video_mp4.value,
            result.video_path,
            metadata={
                "engine": renderer.name,
                "scene_count": len(result.scenes),
                "width": spec.width,
                "height": spec.height,
                "fps": spec.fps,
                "captions_burned": show_captions,
            },
        )
        publish(run_id, {"type": "artifact.created", "artifact_type": "episode_video_mp4", "path": result.video_path})

    for note in result.notes:
        publish(run_id, {"type": "log", "message": f"video: {note}"})

    _end_stage(run_id, Stage.video_compose)
    return run_id


@celery_app.task(name="run.mark_completed")
def mark_completed(run_id: str):
    # Do not resurrect a run the user cancelled.
    if _run_status(run_id) == RunStatus.cancelled.value:
        return run_id
    _update_run(
        run_id,
        status=RunStatus.completed.value,
        stage=Stage.completed.value,
        progress=1.0,
        completed_at=_utcnow(),
    )
    publish(run_id, {"type": "run.completed"})
    return run_id


@celery_app.task(name="run.mark_failed")
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


@celery_app.task(name="run.start_pipeline")
def start_pipeline(run_id: str):
    """Schedule the task graph and return immediately."""
    chain(
        mark_running.s(run_id),
        brain_generate_strategy.s(),
        dialogue_generate_script.s(),
        voice_generate_turn_audio.s(),
        audio_stitch_master.s(),
        video_compose_episode.s(),
        mark_completed.s(),
    ).apply_async(link_error=mark_failed.s(run_id))
    return run_id


@celery_app.task(name="run.start_voice_pipeline")
def start_voice_pipeline(run_id: str):
    """Re-render audio for an existing run without touching its script."""
    chain(
        prepare_voice_rerun.s(run_id),
        voice_generate_turn_audio.s(),
        audio_stitch_master.s(),
        video_compose_episode.s(),
        mark_completed.s(),
    ).apply_async(link_error=mark_failed.s(run_id))
    return run_id


@celery_app.task(name="run.start_video_pipeline")
def start_video_pipeline(run_id: str):
    """Re-cut the video for an existing run without touching its audio."""
    chain(
        prepare_video_rerun.s(run_id),
        video_compose_episode.s(),
        mark_completed.s(),
    ).apply_async(link_error=mark_failed.s(run_id))
    return run_id


# Resolve every task now, on the importing thread.
#
# `@celery_app.task` returns a PromiseProxy, not a Task: creation is deferred
# until the app is finalized. The proxy resolves on first use, and that
# resolution is not thread-safe — two threads touching the same unresolved task
# at once can leave one of them holding a half-bound Task whose `request_stack`
# is still None, which then dies inside `push_request` as:
#
#     AttributeError: 'NoneType' object has no attribute 'push'
#
# That is reachable in production, not just in tests. `_run_detached` starts a
# thread per run whenever the broker is unreachable, so two users starting runs
# at the same moment could crash one of them with an error naming nothing that
# appears in this codebase. Finalising here costs one call at import and makes
# the whole race impossible.
celery_app.finalize()


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
            dialogue_generate_script,
            voice_generate_turn_audio,
            audio_stitch_master,
            video_compose_episode,
            mark_completed,
        ),
    )


def run_voice_pipeline_local(run_id: str) -> str:
    """In-process fallback for a voice-only rerun."""
    return _run_stages_local(
        run_id,
        (
            prepare_voice_rerun,
            voice_generate_turn_audio,
            audio_stitch_master,
            video_compose_episode,
            mark_completed,
        ),
    )


def run_video_pipeline_local(run_id: str) -> str:
    """In-process fallback for a video-only re-render."""
    return _run_stages_local(
        run_id,
        (
            prepare_video_rerun,
            video_compose_episode,
            mark_completed,
        ),
    )


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


def kickoff_video(run_id: str) -> str:
    """Start a video-only re-render. Same contract as `kickoff`."""
    return _kickoff(run_id, start_video_pipeline)


def _kickoff(run_id: str, task) -> str:
    if not broker_reachable():
        return "local"
    try:
        task.delay(run_id)
        return "celery"
    except Exception as e:
        log.warning("celery enqueue failed (%s); running locally", e)
        return "local"
