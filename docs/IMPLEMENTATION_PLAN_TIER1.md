# Autonomous AI Media Engine — Tier 1 Implementation Plan

This document turns the “Text → Multi‑Platform Content Empire” vision into an executable build plan with concrete modules, interfaces, and milestones.

## Outcomes (Definition of Done)

- **Input**: niche + brand profile + constraints (language, tone, platforms, frequency).
- **Output** (per run):
  - A **content strategy**: calendar, topics, hooks, thumbnails/scene prompts, predicted viral score.
  - A **full episode**: multi-agent dialogue script → voiced audio → avatar video (lip sync) + B‑roll scenes.
  - **Shorts pack**: auto-selected clips with burned-in captions + exports for TikTok/IG/YT Shorts + metadata.
- **Product**:
  - Async job system (queue + progress + notifications)
  - Minimal SaaS wrapper UI (upload/choose niche → generate → preview → export)
  - Observability: latency, cost, failures, quality metrics.

## Execution phases (build + ship in 1–3 days per phase)

Rule for this project: **each phase must end with a working vertical slice** and must be independently testable. Update the checkboxes as you complete phases.

### Phase 1 — Orchestrated Brain → Script (Vertical Slice)

- **Status**
  - [ ] Not started
  - [ ] In progress
  - [x] Completed
- **Objective**: Stable async SaaS/API base + “Autonomous Content Brain → multi-agent dialogue JSON” end-to-end.
- **What gets built**
  - FastAPI: `projects`, `runs`, artifacts registry, run cancellation
  - Celery+Redis: staged task graph + retries + progress events (**dev fallback supported when Redis is unavailable**)
  - Content Brain v1: `StrategyPack` (calendar/topics/hooks + baseline viral score features)
  - Script engine: schema-validated `EpisodePlan` + `DialogueTurn[]` JSON
  - Next.js UI: create run → view progress → view strategy + script artifacts
- **What is NOT included yet**
  - TTS/voice cloning, audio mastering, avatar video, B-roll generation, Whisper, clipper, platform exports
- **Output (what works at the end)**
  - User inputs niche/brand/mode → async run produces StrategyPack + dialogue JSON artifacts with live progress
- **Key modules**
  - `apps/api/`, `workers/orchestrator/`, `packages/core/`, `apps/web/`
- **Depends on**
  - None

### Phase 2 — TTS + Audio Mastering (Vertical Slice)

- **Status**
  - [ ] Not started
  - [ ] In progress
  - [x] Completed
- **Objective**: Deterministically convert dialogue JSON into mastered episode audio.
- **What gets built**
  - VoiceRegistry + per-speaker voice assignment (**shipped against the local system engine via pyttsx3/SAPI**; Fish Speech / XTTS deferred)
  - Map TTS over `DialogueTurn[]` → per-turn WAV artifacts
  - Audio stitch/master: pauses, normalization, optional music ducking → `episode.wav` + `episode.mp3`
    - **Shipped**: RMS normalization with a true-peak ceiling (`workers/media/audio/normalize.py`). Full EBU R128/LUFS gating and music ducking are still open — Python 3.13 dropped `audioop`, so this is a dependency-free implementation over `array`. Swapping in real LUFS metering only changes `_measure`.
    - **Shipped**: MP3 export shells out to FFmpeg and is best-effort — a machine without FFmpeg still completes the run with `episode.wav` only.
  - UI: preview per-turn and full episode audio; “regenerate voice only” without regenerating script
    - **Shipped**: `POST /runs/{id}/regenerate-voice` reruns only the TTS + stitch stages against the existing `dialogue_turns.json`, purging the previous audio artifacts first. Accepts optional per-speaker `voice_overrides` (ids from `GET /voices`).
- **What is NOT included yet**
  - Avatar/lip-sync video, automated B-roll generation, Whisper alignment, clip extraction, platform exports
- **Output (what works at the end)**
  - Same run now produces playable mastered audio + per-turn WAVs, tracked as artifacts
- **Key modules**
  - `workers/media/tts/`, `workers/media/audio/`, plus Phase 1 modules for orchestration/UI
- **Depends on**
  - Phase 1 schemas + run/orchestration + artifact storage

### Phase 3 — Avatar Lip-Sync Video + Captions (Vertical Slice)

- **Status**
  - [ ] Not started
  - [ ] In progress
  - [x] Completed
- **Objective**: Produce a watchable full episode video from mastered audio.
- **What gets built**
  - MuseTalk video stage (LivePortrait fallback) driven by `episode.wav`
    - **Deferred.** Both need a GPU and downloaded model weights, which would make
      the video stage fail on every machine that lacks them. What shipped instead is
      the `VideoRenderer` seam (`workers/media/video/base.py` + `registry.py`) with a
      pure-Python compositor registered against it. A neural backend is a
      registration, not an orchestrator change — see ARCHITECTURE.md. **This is the
      one Tier-1 capability not yet real: the episode is a turn-aware cut, not a
      lip-synced avatar.**
  - Minimal compositor: turn-aware cuts (or single track), speaker lower-thirds, burned captions (first-pass)
    - **Shipped**: `workers/media/video/slideshow.py` — a two-up speaker stage where
      the talking speaker is lit and the other dimmed, accent-coloured lower-thirds,
      burned captions and a progress ticker. Draws through `raster.py`, a 5x7-font
      software rasterizer, so the stage has no imaging dependency (the same rule
      `normalize.py` follows). Frames are PPM; FFmpeg's concat demuxer expands one
      frame per caption to a constant frame rate, so a 60s episode is a few dozen
      images rather than 1,500.
    - Captions are derived from `episode_timeline.json`, so they are frame-accurate
      at every turn edge and drift at most one chunk mid-turn. Word-level accuracy
      is the Phase 4 Whisper stage; only `build_captions`'s input changes.
    - **Font covers printable ASCII only** — non-Latin captions render as `?` and
      need a real font stack, which lands with the Phase 5 multilingual work.
  - B-roll support as *overlay slots* from `broll_cues` (static images acceptable in this phase)
    - **Shipped as wired-but-empty**: cues travel from the script into a titled slot
      panel in the frame. The panel shows the cue text rather than generated imagery —
      filling it needs the Stable Diffusion asset pipeline, scheduled after the clip engine.
  - UI: episode video preview + download
    - **Shipped**: an Episode video card with an inline player, mp4 and `.srt`
      downloads, a storyboard inspector, and a re-cut control.
  - **Added**: `POST /runs/{id}/regenerate-video` — the cheapest re-render in the
    pipeline. Script and every WAV are reused; only the compositor runs again, so
    iterating on the look does not pay for another TTS pass. Toggles burned captions
    and B-roll slots.
- **What is NOT included yet**
  - Whisper alignment (for word-perfect captions), automated clip engine, platform exports, full automated B-roll generation
  - Lip sync itself (see above), and the `cpu/gpu` queue split — still one Celery queue
- **Output (what works at the end)**
  - A complete lip-synced avatar episode video artifact generated via the async run
    - **Actual**: a complete, watchable *composed* episode video (`episode.mp4`) plus
      `captions.srt` and `storyboard.json`, generated via the async run. Without FFmpeg
      the run still completes with the storyboard and captions.
- **Key modules**
  - `workers/media/video/` (`base`, `registry`, `slideshow`, `captions`, `raster`, `mux`),
    `workers/media/ffmpeg.py`, plus Phase 1–2 modules
- **Depends on**
  - Phase 2 mastered audio + Phase 1 dialogue JSON

### Phase 4 — Whisper + Clip Engine + Platform Exports (Vertical Slice)

- **Status**
  - [ ] Not started
  - [ ] In progress
  - [ ] Completed
- **Objective**: Generate a ranked shorts pack (viral detection → clips → exports) from the episode.
- **What gets built**
  - Whisper transcript + timestamps artifacts
  - Clip candidate generation + scoring (text saliency + prosody/energy + silence structure + basic scene-change heuristics)
  - Render 3–10 clips in 9:16 with burned captions + `metadata.json` per clip
  - UI: ranked clips list (viral score + reason tags) + previews + download bundle
- **What is NOT included yet**
  - Trained viral model, multilingual variants, auto content loop, “perfect” B-roll automation
- **Output (what works at the end)**
  - End-to-end run produces a full episode + a downloadable shorts pack ready for TikTok/IG/YT Shorts
- **Key modules**
  - `workers/media/asr/`, `workers/media/clips/`, `packages/core/scoring/`, `workers/media/video/export/`
- **Depends on**
  - Phase 3 episode video (and Phase 2 audio + Phase 1 timing cues)

### Phase 5 — Moat Hardening (Multilingual + Brand Clone + Auto Loop + Reliability)

- **Status**
  - [ ] Not started
  - [ ] In progress
  - [ ] Completed
- **Objective**: Convert the working engine into a repeatable content business generator.
- **What gets built**
  - Multilingual variants: style-preserving translation → per-language TTS → per-language exports
  - Personal brand cloning: saved brand doctrine + visual kit + safety boundaries; reusable presets
  - Auto content loop: scheduled runs (Celery beat/cron), topic queue → publish-ready packs
  - Reliability: CPU/GPU queue separation, timeouts, idempotency, caching for B-roll assets, signed URLs
  - Viral score evolution path: log features → offline eval harness → trainable ranker later (without blocking runtime)
- **What is NOT included yet**
  - “Big product” scope (teams, payments) unless explicitly prioritized
- **Output (what works at the end)**
  - Daily (or scheduled) autonomous multi-language content generation with stable personas + measurable quality/latency
- **Key modules**
  - `workers/orchestrator/schedules/`, `workers/media/i18n/`, `packages/core/brand/`, infra/storage modules
- **Depends on**
  - Phases 1–4 end-to-end pipeline

## Architecture (System-level)

### Core services

- **Web App (Next.js + shadcn/ui)**
  - Project setup, runs creation, preview players, exports, billing-ready structure.
- **API (FastAPI)**
  - Auth (optional in v1), project + run orchestration, asset registry, webhooks.
- **Orchestrator (Celery + Redis)**
  - Long-running media jobs: LLM → TTS/voice clone → render → clipper → export.
- **Media Worker(s)**
  - GPU/CPU separated pools (voice/video on GPU where needed, FFmpeg on CPU).
- **Storage**
  - Local dev: filesystem
  - Production: S3-compatible (R2/MinIO/S3) for assets + signed URLs.
- **DB**
  - Postgres for projects/runs/assets/metrics (SQLite ok for day-1 dev).

### High-level data flow

1. **Brain** produces strategy + episode plan.
2. **Script** produces multi-agent dialogue JSON (structured, time-aware).
3. **Voice** clones/assigns voices → generates per-turn WAV.
4. **Audio** stitches, normalizes, adds pauses/music, masters final.
5. **Video** generates talking heads (MuseTalk/LivePortrait), overlays captions, inserts B‑roll.
6. **Clipper** uses Whisper + scene detection + emotion/saliency → selects highlights.
7. **Exporter** renders platform variants + upload bundles + metadata.

## Repo layout (actual)

Kept in sync with the tree. Directories marked *(planned)* do not exist yet.

```
MyVidCraft.ai/
  apps/
    web/                 # Next.js 15 + React 19 (plain CSS; shadcn not adopted)
    api/                 # FastAPI (REST + websocket)
  workers/
    orchestrator/        # Celery app, task graph, and the Brain/Dialogue engines
      brain.py           #   niche + brand -> StrategyPack
      dialogue.py        #   -> EpisodePlan + DialogueTurn[]
      llm.py             #   Anthropic wrapper; never raises into the pipeline
      tasks.py           #   task graph + in-process fallback
    media/
      ffmpeg.py          #   one place that locates the binary; optional everywhere
      tts/               #   SpeechEngine protocol + registry + cloud HTTP impl
      audio/             #   stitch, normalize, encode
      video/             #   VideoRenderer protocol + registry + slideshow compositor
        raster.py        #     software rasterizer (no imaging dependency)
        captions.py      #     timeline -> captions -> SRT
        mux.py           #     concat playlist -> mp4
      asr/  clips/       #   (planned, Phase 4)
  packages/
    core/myvidcraft_core/  # shared schemas + the Stage/progress model
    prompts/               # prompt templates as versioned .md files
  docs/
    IMPLEMENTATION_PLAN_TIER1.md
    ARCHITECTURE.md      # engine seams, artifact contract, degradation rules
  tests/
```

### Naming rule

Modules, Celery tasks and stages are named after the **engine** that owns them,
never after the phase that introduced them. A phase number describes when
something was built, which stops being useful once it is built.

| Stage / task name | Module |
|---|---|
| `brain.generate_strategy` | `workers/orchestrator/brain.py` |
| `dialogue.generate_script` | `workers/orchestrator/dialogue.py` |
| `voice.generate_turn_audio` | `workers/media/tts/` |
| `audio.stitch_master` | `workers/media/audio/` |
| `video.compose_episode` | `workers/media/video/` |
| `run.*` (lifecycle) | `workers/orchestrator/tasks.py` |

## “Autonomous Content Brain” (Core Differentiator)

### Inputs

- **Niche profile**: domain, target audience, taboo topics, monetization goal.
- **Brand profile**: voice/tone, opinions/stance, catchphrases, pacing, language(s).
- **Platform profile**: TikTok/IG/YT/podcast, length constraints, CTA style.
- **History**: prior performance (optional), existing content embeddings.

### Outputs (artifacts)

- `StrategyPack`:
  - **Content calendar** (7–30 days): topics, formats, posting times, CTA variants.
  - **Viral topic slate**: 20–50 candidate topics with predicted reach.
  - **Hook library**: 30 hooks tailored to the niche + brand.
  - **Episode blueprint**: “beats” (setup → conflict → payoff), intended emotions.
  - **Asset prompts**: thumbnails, B‑roll prompts, scene outlines.

### Brain components

- **Trend miner (v1 heuristic + optional web later)**
  - Start with deterministic “trend templates” per niche:
    - “X is lying to you about Y”
    - “What nobody tells you about Z”
    - “Do this for 7 days — results”
  - Add an optional “sources plugin” later (Reddit/X/YouTube RSS) when ready.
- **Hook predictor**
  - Features:
    - contradiction intensity, curiosity gap, specificity, authority cues, novelty, brevity
  - Outputs:
    - `hook_score` 0–1
    - `dropoff_risk` estimate
    - suggested hook rewrites
- **Engagement optimizer**
  - Turns the same idea into variants:
    - debate mode vs storyteller mode
    - soft CTA vs hard CTA
    - comedic vs serious

### Deliverable JSON schema (v1)

- `ContentCalendarItem`: date, platform, topic, hook, format, CTA, asset_prompts.
- `EpisodePlan`: title, premise, stance, beats[], characters[], constraints.

## Multi-Agent Personalities (Host/Guest engine)

### Personality model

Define each character as a stable bundle:

- `name`, `role` (host/guest), `voice_id`
- `tone` (calm/aggressive/funny), `rhetoric` (storyteller/debater/teacher)
- `beliefs` (opinions to lean into), `taboos`, `catchphrases`
- `interrupt_style` (none/rare/often), `humor_level`

### Dialogue output format (non-negotiable)

Produce **structured dialogue JSON** so downstream tools can render deterministically.

- `DialogueTurn`:
  - `speaker`
  - `text`
  - `emotion` (neutral/excited/sarcastic/angry/etc.)
  - `pace_wpm` (target)
  - `pause_before_ms`, `pause_after_ms`
  - `broll_cues[]` (what should appear on screen)
  - `safety_flags[]` (optional)

### Modes

- **Debate mode**: disagreement, interruptions, rhetorical questions, “gotcha” beats.
- **Storytelling mode**: arc, cliffhangers every 20–40s, sensory language cues.
- **Teaching mode**: numbered steps, examples, recap loop.

## Full Media Pipeline (Implementation Detail)

### 1) Script generation (LLM)

- Primary: **Llama via Groq** (fast/cheap)
- Backup: Claude (quality fallback)
- Must support:
  - JSON schema enforcement
  - revision passes (hook tighten, filler removal, shorter sentences for TTS)

**Key constraint**: downstream timing depends on clean turns (short sentences, explicit pauses).

### 2) Voice cloning / TTS

- Primary: **Fish Speech** (voice quality)
- Alternatives: GPT-SoVITS, XTTS

Implementation requirements:
- Maintain a `VoiceRegistry`:
  - training source(s), language coverage, style prompts, safety notes
- Generate per-turn audio with:
  - consistent loudness targets (LUFS)
  - controlled pauses
  - per-speaker style tokens (if supported)

### 3) Audio stitching + mastering

Tools: FFmpeg + PyDub + Librosa

Steps:
- normalize each turn
- insert pauses (from JSON)
- optionally add bed music (ducking under speech)
- output:
  - `episode.wav`
  - `episode.mp3`
  - `turns/turn_####.wav`

### 4) Avatar video + lip sync

- Primary: **MuseTalk**
- Secondary: **LivePortrait**

Video composition strategy (v1):
- Talking head base track per speaker (or one host avatar for both, if easiest)
- Cut on turns; overlay lower-third speaker label
- Add kinetic captions (word-level from Whisper alignment)
- Optional: picture-in-picture B‑roll window

### 5) Auto B-roll + scene generation

- Local: Stable Diffusion via ComfyUI pipelines
- Optional: Runway (later)

Plan:
- Convert `broll_cues` into prompts
- Generate:
  - still images (fast)
  - short loops (later)
- Keep a `BrollAsset` library to reuse across runs to reduce compute.

### 6) Clip extraction (viral detection engine)

Inputs:
- transcript (Whisper)
- turn boundaries + emotions
- audio energy
- scene cuts

Signals (v1):
- **Text saliency**: punchlines, controversy, numbers, “you” language
- **Prosody**: pitch/energy spikes (Librosa)
- **Silence structure**: suspense pauses before payoff
- **Visual**: scene changes, face close-up stability (YOLO + tracking)

Output:
- ranked `ClipCandidate`s with:
  - start/end timestamps
  - title + hook caption
  - viral score + reason tags

### 7) Multi-platform export

Render variants:
- 9:16 (TikTok/Reels/Shorts)
- 1:1 (optional)
- 16:9 (YouTube/podcast video)

Deliverables per clip:
- `video.mp4`
- `captions.srt` + burned-in variant
- `metadata.json` (title, description, hashtags, CTA)

## “Non‑negotiables” moats (build into v1 design)

### Viral Score Predictor (v1 practical)

Ship a **baseline scoring model** immediately:
- heuristic score (fast, no training)
- log every feature + predicted score + actual outcomes (later)

Feature groups:
- hook strength (first 2–3 seconds)
- controversy/novelty
- specificity (numbers, timeframes, named entities)
- pacing (sentence length, pause cadence)

Longer-term:
- train a regression/ranker on your own dataset (CTR, retention, shares)

### Multilingual voice (same persona across languages)

v1 approach:
- translate script (LLM) with **style preservation**
- language-specific TTS pass
- keep consistent voice where possible (XTTS can help)

Deliverable:
- `RunVariant` per language with shared timing anchors if feasible.

### Personal brand cloning (AI influencer)

Store:
- brand doctrine (opinions, phrases)
- visual kit (avatar, colors, intro/outro)
- forbidden zones (compliance + reputational)

### Auto content loop

Scheduler:
- daily job triggers (Celery beat / cron)
- topic queue → generation → publish-ready pack

## APIs (FastAPI)

### Core endpoints (v1)

- `POST /projects`
- `GET /projects/{id}`
- `POST /projects/{id}/runs` (start a generation run)
- `GET /runs/{id}` (status + progress)
- `GET /runs/{id}/artifacts` (signed URLs)
- `POST /runs/{id}/cancel`
- `POST /webhooks/render-complete` (optional; for external GPU providers)

### Real-time progress

- WebSocket: `/ws/runs/{id}`
- Events:
  - `run.started`
  - `stage.started` / `stage.progress` / `stage.completed`
  - `artifact.created`
  - `run.failed` / `run.completed`

## Data model (DB)

Minimum tables:

- `projects`: owner, niche, brand_profile_json, defaults_json
- `runs`: project_id, status, started_at, completed_at, error, config_json
- `artifacts`: run_id, type (script/audio/video/clip/broll), path, metadata_json
- `metrics`: run_id, stage, latency_ms, cost_estimate, quality_json

Key principle: **every artifact is addressable** and reproducible from inputs.

## Task graph (Celery)

Stages (each emits progress + artifacts):

1. `brain_generate_strategy`
2. `script_generate_dialogue_json`
3. `voice_prepare_registry` (if needed)
4. `tts_generate_turn_audio` (map over turns)
5. `audio_stitch_master`
6. `video_generate_talking_head`
7. `broll_generate_assets`
8. `video_compose_final`
9. `asr_whisper_transcribe_align`
10. `clip_detect_candidates`
11. `render_export_variants`

Reliability:
- idempotent tasks (safe retries)
- deterministic paths (`run_id/…`)
- checkpointing after each stage

## Compute + Infra (free-hack → scale)

### Dev
- Local CPU for orchestration + FFmpeg
- Optional: Colab for GPU stages (manual first)

### Prototype production (fast)
- HuggingFace Spaces for demo
- One GPU worker (RunPod) for video/TTS-heavy stages

### Scale-ready
- Separate queues:
  - `cpu_low`, `cpu_high`, `gpu_low`, `gpu_high`
- Autoscaling GPU workers
- S3 storage + CDN for artifacts

## UX (Latency is the real bottleneck)

### Must-have UX patterns
- “Start run” returns instantly with `run_id`
- progress timeline (stages + ETA)
- partial preview:
  - show script immediately
  - play stitched audio before video finishes
  - show first 1–2 clips early

### Quality controls
- “Regenerate hook only”
- “Make it more controversial/safer”
- “Shorter sentences for TTS”
- “More jokes / less jargon”

## Testing + Observability

- Unit tests (`pytest`, see `tests/`):
  - [x] JSON schema validation
  - [x] loudness normalization + WAV stitching (pause insertion, param-mismatch rejection)
  - [x] voice casting (role preferences, determinism, wrap-around)
  - [x] FFmpeg MP3 export (encode tests skip automatically when FFmpeg is absent)
  - [x] API surface + the voice-only rerun (artifact purge, run reset, override handling)
  - [x] caption segmentation + SRT (contiguity, no drift, no overlapping cues)
  - [x] the software rasterizer (clipping, alpha, font metrics, PPM output)
  - [x] the concat playlist + mux guards (bare filenames, repeated last frame)
  - [x] the compositor (gapless scenes, even dimensions, degradation without FFmpeg)
  - [x] the video-only re-render (purge asymmetry vs. a voice rerun, frame cleanup, API surface)
  - [ ] clip scoring functions (Phase 4)
- Integration tests:
  - [x] a “tiny run” end-to-end (`tests/test_pipeline_integration.py`) — a fake
    `SpeechEngine` writing 20ms of silence makes the whole chain run in milliseconds,
    which is the second payoff of putting synthesis behind a protocol. Covers the
    timeline matching the audio actually written, the storyboard covering the audio
    with no gaps, and both rerun paths. The API tests still stub kickoff, because a
    live run drives the system speech engine for minutes.
- Metrics:
  - stage latency
  - GPU/CPU utilization
  - artifact sizes
  - failure rates

## Security + Compliance (pragmatic v1)

- Store only what you need:
  - voice sources are sensitive; keep access controlled
- Add a “content safety” pass for:
  - hate/harassment
  - medical/financial high-risk claims
- Logging:
  - redact prompts/PII by default

## 7-Day Build Plan (Optimized execution)

### Day 1–2: Brain + Script + Multi-agent JSON

- Implement `StrategyPack` + `EpisodePlan` schemas.
- Implement script generator with deterministic JSON output.
- Add personality presets + mode toggles.
- API endpoints to create a run and return script artifacts.

### Day 3: Voice cloning integration

- Integrate Fish Speech (or fastest workable TTS first).
- Generate 2-speaker per-turn WAVs.
- Store artifacts + per-turn metadata.

### Day 4: Audio stitching

- Build masterer: normalize, pauses, music ducking.
- Produce `episode.wav` + `episode.mp3`.
- UI: audio preview player.

### Day 5: Avatar video (lip sync)

- Integrate MuseTalk (fallback LivePortrait).
- Render talking head video aligned to `episode.wav`.
- Compose captions overlay (rough, refine later).

### Day 6: Clip engine

- Whisper transcription + word timestamps.
- Implement baseline clip scoring:
  - punchline detection + energy spikes + silence heuristics.
- Export 3–10 ranked shorts with captions.

### Day 7: UI + SaaS wrapper

- Web UI flow: niche → generate → progress → preview → download.
- Export presets (TikTok/IG/YT).
- Add run history + artifact gallery.

## Next upgrades (Weeks 2–4)

- True B‑roll generator pipeline with caching + prompt tuning.
- Visual tracking (YOLO + DeepSort) for robust scene/face logic.
- Multilingual variants with “same persona” style preservation.
- Feedback loop:
  - user edits + publish outcomes → improve predictor.

## MVP acceptance checklist

- [x] Start a run in < 2 seconds (async kickoff) — the broker is TCP-probed before enqueue, so an unreachable Redis fails over to in-process execution instantly instead of blocking on the kombu retry budget
- [x] Script JSON validates (100% of runs) — schema-enforced, with a deterministic template fallback when no LLM key is configured
- [x] Audio generated and playable — per-turn WAVs + mastered `episode.wav` (`episode.mp3` when FFmpeg is present)
- [~] Video render completes with lip sync — the render completes and is watchable,
  and is **verified end to end**: a 60s episode muxes to h264 1280x720 @ 25fps + aac
  with a duration matching the audio exactly (~10s to render and mux). But it is a
  turn-aware composed cut, **not** lip sync. MuseTalk/LivePortrait are deferred behind
  the `VideoRenderer` seam; this box is not honestly checkable until one is registered.
- [ ] At least 3 clips exported with captions
- [x] Artifact URLs stable and downloadable — `/artifacts/{id}/file`, path-guarded to the artifacts dir
- [ ] Basic metrics captured per stage — the `metrics` table is still unbuilt; stage timing is only visible as progress events

