# Architecture

How the system is put together, and the rules a change should follow. The
build-order roadmap lives in [IMPLEMENTATION_PLAN_TIER1.md](IMPLEMENTATION_PLAN_TIER1.md);
this document describes the shape, not the schedule.

## Engines, not phases

The pipeline is a sequence of **engines**. Each owns one transformation, reads
artifacts produced upstream, and writes artifacts of its own.

```
Brain      niche + brand              -> StrategyPack
Dialogue   niche + brand + mode       -> EpisodePlan + DialogueTurn[]
Voice      DialogueTurn[]             -> per-turn WAVs
Audio      per-turn WAVs + pauses     -> episode.wav (+ episode.mp3) + episode_timeline.json
Video      episode.wav + timeline     -> episode.mp4 + captions.srt + storyboard.json
ASR        episode.wav                -> transcript + timings   (Phase 4, not built)
Clips      transcript + audio + video -> ranked clips + exports  (Phase 4, not built)
```

Modules, tasks and stages are named after the engine that owns them. Phase
numbers describe *when something was built*, which stops being useful the moment
it is built — they do not appear in identifiers.

## Artifact contract

Every stage output is a file on disk plus a row in `artifacts`. Nothing is passed
between stages in memory, and no stage depends on another's internal state.

This is what makes stages independently runnable: a voice-only rerun re-executes
the Voice and Audio engines against the existing `dialogue_turns.json` without
the Brain or Dialogue engines being involved. A video-only rerun does the same
one stage further down, against `episode_timeline.json`.

The two reruns are deliberately asymmetric, and getting this backwards is the
bug worth naming: new audio invalidates the video cut against it, so a **voice**
rerun replaces the video artifacts too, while a **video** rerun leaves every
audio artifact alone. Otherwise a run ends up with an `episode.mp4` whose cuts
and captions no longer match the WAV beside it.

`episode_timeline.json` is the hand-off between the Audio and Video engines. The
stitcher counts *frames actually written*, so it publishes the real offsets
rather than letting the compositor re-derive them from pause values and drift out
of sync with the file it is cutting. Phase 4's clip engine reads the same artifact.

Paths are deterministic (`{artifacts_dir}/{run_id}/...`) and every read of an
artifact path is confined to the artifacts directory before it is opened or
deleted — see `_resolve_artifact_path` in `apps/api/main.py` and `_safe_unlink`
in `workers/orchestrator/tasks.py`.

**Frames are the exception to "every output is kept."** The compositor writes
uncompressed PPM — about 2.7 MB per frame at 720p, so a one-minute episode is
well over 100 MB of them. They are an *intermediate* when the mux succeeds and
the *deliverable* when it does not, so the video stage discards them only once an
`episode.mp4` exists, and `storyboard.json` records which happened in
`frames_kept`. A re-render clears them up front as well (`_clear_frames_dir`),
because the compositor names frames by index and a shorter re-cut would otherwise
strand the tail of the previous one.

## Stages and progress

`packages/core/myvidcraft_core/stages.py` is the single source of truth for
stage names and the progress bar.

A stage declares a **relative weight**, not a progress number. Bounds are derived
from the weights, so adding a stage rebalances the bar automatically:

```python
STAGE_WEIGHTS = {
    Stage.brain_strategy:   1.0,
    Stage.dialogue_script:  2.0,
    Stage.voice_turns:      5.0,   # drives a real speech engine, turn by turn
    Stage.audio_master:     1.0,
}
```

Tasks call `_begin_stage` / `_end_stage` rather than writing stage names and
progress floats by hand. Long stages report intermediate progress with
`progress_at(stage, fraction)`, which clamps — a task that miscounts its own
items cannot push the bar into the next stage's range.

`Run.stage` stays a plain `str` in the API schema because runs created before
this enum existed hold free-form values and must remain readable.

## Swappable implementations

Where a component has a committed second implementation, it sits behind a
protocol and is resolved by name. Where it does not, it is a plain module.

| Seam | Protocol | Registry | Implementations |
|---|---|---|---|
| Speech synthesis | `workers/media/tts/base.py` | `workers/media/tts/registry.py` | `pyttsx3` (system/SAPI) |
| Video rendering | `workers/media/video/base.py` | `workers/media/video/registry.py` | `slideshow` (pure-Python compositor) |
| LLM | `workers/orchestrator/llm.py` — `generate_structured()` | — | Anthropic |

**Adding a speech engine:**

1. Implement `SpeechEngine` (`list_voices`, `synthesize`) in
   `workers/media/tts/<name>.py`.
2. `register("<name>", factory)` in `workers/media/tts/registry.py`. Register the
   *factory*, not an instance — a GPU backend must not import on a machine that
   never uses it.
3. Set `MYVIDCRAFT_TTS_ENGINE=<name>`.

Nothing in the orchestrator changes. `tests/test_tts_registry.py` and
`tests/test_pipeline_integration.py` both run against fakes, which is the
seam's second payoff: the end-to-end test does not need a real speech engine.

**Adding a video renderer** (this is how MuseTalk/LivePortrait land):

1. Implement `VideoRenderer` (`available`, `render`) in
   `workers/media/video/<name>.py`. It receives an `EpisodeVideoSpec` — audio,
   turn timeline, captions, look — and returns a `RenderResult`.
2. `register("<name>", factory)` in `workers/media/video/registry.py`, factory
   again rather than instance: a MuseTalk registration pulls in torch and model
   weights, which must not load on a machine that only runs the compositor.
3. Set `MYVIDCRAFT_VIDEO_ENGINE=<name>`, and re-weight `Stage.video_compose` in
   `stages.py` — a neural backend would be the heaviest stage in the pipeline,
   and the progress bar rebalances from the weights.

`available()` is the part of the protocol that earns its keep. Video backends
have heavy external requirements, and returning `(False, reason)` must not mean
"do nothing": a renderer is still expected to emit whatever partial output it
can, and the reason is surfaced on the run so a missing dependency is
diagnosable from the UI instead of from a worker log. `GET /health` reports it
before a run is even started.

Everything upstream of `render()` — timeline extraction, caption segmentation,
artifact registration, progress — is renderer independent and already lives in
the orchestrator, so a new backend inherits all of it.

The LLM wrapper is deliberately *not* a registry. `generate_structured()` is
already provider-agnostic at the signature; adding a second provider means
writing one function body. A registry with one implementation would be
indirection without a caller.

## Prompts

Prompt text lives in `packages/prompts/` as `.md` files, addressed by path
(`render("brain/strategy.system", days=7)`).

Required parameters are **derived from the template**, so they cannot drift from
the text. `render()` raises on a missing *or* unexpected parameter — a missing
one would otherwise leave a literal `{days}` in a prompt the model answers
anyway, badly. `tests/test_prompts.py` renders every shipped prompt, so a broken
placeholder fails in CI rather than at the first paid API call.

## Execution model

Runs are async and never block the request that starts them.

- `kickoff()` TCP-probes the broker first. `.delay()` against an unreachable
  Redis blocks for the full kombu retry budget (~110s observed), against a
  sub-2-second target for starting a run.
- No broker reachable → the pipeline runs in a **detached daemon thread**
  (`_run_detached`). Not `BackgroundTasks`, which would hold the request's
  resources for a multi-minute render.
- Progress events publish to Redis pubsub behind a circuit breaker
  (`workers/orchestrator/progress.py`). Telemetry must never stall a run, so a
  failure mutes publishing for 30s instead of re-paying the socket timeout on
  every event.
- `/ws/runs/{id}` streams from Redis, falling back to DB polling. Once an event
  has been delivered from Redis, a later failure propagates rather than silently
  re-streaming from the polling path.

Every stage calls `_guard()`, which aborts if the run was cancelled or already
finished. Cancellation is a DB write; workers observe it between stages and
between turns.

## Degradation rules

The system runs with nothing installed but Python. Each optional dependency
degrades rather than failing a run:

| Missing | Effect |
|---|---|
| Anthropic API key | Brain and Dialogue use deterministic templates |
| Redis | Runs execute in-process; no live progress events |
| FFmpeg | `episode.wav` only, no MP3; video degrades to storyboard + `captions.srt`, no mp4 |
| System voices | All speakers share the engine default |
| GPU / model weights | No lip sync; the compositor cuts the episode instead |

The video stage is the sharpest case: the frames and the storyboard are real
output whether or not the mux succeeds, so a machine with no FFmpeg still
finishes the run and records *why* there is no mp4 in the storyboard's `notes`
and on the run's event stream.

Drawing has no imaging dependency at all — `raster.py` is an RGB byte buffer, a
5x7 bitmap font and binary PPM output, for the same reason `normalize.py`
implements loudness over `array` rather than pulling in pydub. The cost is that
the font covers printable ASCII only; anything else renders as `?`, so non-Latin
captions need a real font stack alongside the Phase 5 multilingual work.

`llm.py` never raises into the pipeline: every failure mode — no key, refusal,
rate limit, network error, schema mismatch — returns `None` and the caller falls
back to a template. A run always produces artifacts.

`GET /health` reports which of these are actually available.

## Known gaps

Tracked here so they are not rediscovered:

- **No lip sync.** The headline Tier-1 video capability is still deferred. The
  seam exists and the compositor fills it, but MuseTalk/LivePortrait need a GPU
  and model weights. Registering one is the single largest outstanding item.
- **No `metrics` table.** Stage timing exists only as ephemeral progress events.
  Still the next scheduled work, and more pressing now that the pipeline has a
  render stage whose cost varies by orders of magnitude with the backend.
- **One Celery queue.** The plan calls for `cpu_low/cpu_high/gpu_low/gpu_high`.
  The compositor is CPU-only so this stayed unbuilt through Phase 3; it becomes
  load-bearing the moment a GPU renderer is registered, and should land with it.
- **B-roll slots are wired but empty.** `broll_cues` now travel from the Dialogue
  engine into the frame, but the slot shows the cue text rather than generated
  imagery. Filling it needs the Stable Diffusion asset pipeline.
- **Captions are apportioned, not aligned.** Frame-accurate at every turn edge,
  drifting at most one chunk mid-turn. Phase 4's Whisper stage replaces
  `build_captions`'s input without changing the module's shape.
- **The caption font is ASCII-only.** See the degradation table above.
- **Storage is local filesystem only.** S3 + signed URLs are Phase 5.
- **Viral score is a hash, not a model.** `_stable_float_0_1` in `brain.py` is
  deterministic but carries no signal on the template path. Real feature
  extraction is Phase 4.
