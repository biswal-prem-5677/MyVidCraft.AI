# MyVidCraft.ai

Text in, finished episode out: a niche becomes a content strategy, a two-speaker
dialogue script, mastered audio, and a cut video with burned captions — as an
async run you can watch stage by stage.

## Where the project actually is

Progress is tracked as **gates**, not phases. Each gate ends in a go/no-go
decision, and the ladder lives in [`docs/EXECUTION_RULES.md`](docs/EXECUTION_RULES.md).

| Gate | Meaning | State |
|---|---|---|
| 0 — Baseline | Pipeline runs end to end on one machine | ✅ Complete |
| **1 — Deployable** | Runs somewhere that is not the author's laptop | 🔵 **Current** — episode rendered, awaiting the listen test |
| 2 — Usable by a stranger | Auth, ownership, run history, rate limiting | ⚪ Blocked |
| 3 — Worth paying for | Billing, unit-cost metrics, retention | ⚪ Blocked |
| 4 — Expand | Whisper transcripts, clip engine | ⚪ Blocked |

> **Historical note.** Earlier commits and `docs/IMPLEMENTATION_PLAN_TIER1.md`
> talk about Phases 1–5. That model was replaced by the gate ladder — Phase 1
> (runnable API/UI + orchestration), Phase 2 (episode audio) and Phase 3
> (composed video) are all shipped and now sit inside Gate 0. If the two
> disagree, `EXECUTION_RULES.md` wins.

**What works today:** strategy generation, dialogue scripting, per-turn speech,
loudness-normalised episode audio, MP3 export, composed slideshow video with
burned captions and SRT, regenerate-voice and regenerate-video as separate cheap
operations, live per-stage progress over websocket, and an artifact browser.

**What does not exist yet:** accounts, login, ownership, billing, run history,
object storage. There is no access control of any kind, so **do not expose this
to the internet.** Keep it on localhost or a private network until Gate 2.

---

## Quick start (no account, no card, ~10 minutes)

### Prerequisites

| | Needed for | Install |
|---|---|---|
| Python 3.11+ | Everything | you have 3.13 |
| Node 20+ | The web UI | you have 22 |
| **FFmpeg** | MP3, MP4, and the **free** speech engine | `winget install Gyan.FFmpeg` |
| Redis | Optional — without it runs execute in-process | `docker run -p 6379:6379 -d redis:7` |

FFmpeg is optional for the project as a whole, but **required if you use the free
speech engine**, which converts its output to WAV. Reopen your terminal after
installing so it lands on `PATH`.

### 1. Install

```powershell
cd "c:\เอกสาร\MyVidCraft.ai"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If PowerShell refuses to run the activate script:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
```

### 2. Choose a speech engine

The voice stage has no offline fallback — unlike the LLM, which degrades to a
template generator. Copy `.env.example` to `.env` and pick one:

**Free, no key, no account** — use this to answer Gate 1's listen test:

```ini
MYVIDCRAFT_TTS_ENGINE=edge
```

Renders the same Microsoft neural voices Edge uses for read-aloud. Requires
FFmpeg. Do not bill customers on it: the endpoint is undocumented, rate-limits
without notice, and periodically returns 403 until `edge-tts` ships a new
release.

**Paid — what the project ships on:**

```ini
MYVIDCRAFT_TTS_ENGINE=openai
MYVIDCRAFT_OPENAI_API_KEY=sk-...
```

Switching between them is that one setting and nothing else — that is what the
`SpeechEngine` protocol in `workers/media/tts/base.py` exists for.

**Script quality is a separate, optional key.** With no
`MYVIDCRAFT_ANTHROPIC_API_KEY` the brain and script engines fall back to a
deterministic template generator: valid, schema-correct, and generic. Runs still
complete. Add a key for real scripts.

### 3. Run it

You do **not** need a Celery worker for local use. With no broker reachable, the
API executes runs in detached threads.

```powershell
# terminal 1 — API
cd "c:\เอกสาร\MyVidCraft.ai"
.\.venv\Scripts\Activate.ps1
uvicorn apps.api.main:app --reload --port 8000
```

```powershell
# terminal 2 — web UI (no venv; this is Node)
cd "c:\เอกสาร\MyVidCraft.ai\apps\web"
npm install
npm run dev
```

- Web UI — <http://localhost:3000>
- API docs — <http://localhost:8000/docs>
- Readiness — <http://localhost:8000/health> reports whether the LLM, the broker,
  FFmpeg and the video renderer are actually available

### 4. Generate an episode

In the UI: set a **Niche**, then **Create project + start run**. Stages run in
order — `brain.generate_strategy` → `dialogue.generate_script` →
`voice.generate_turn_audio` (the long one) → `audio.stitch_master` →
`video.compose_episode` — with the bar moving inside the voice stage.

When it completes, the **Artifacts** card gives `episode.wav` an inline player.
Play it. That is the Gate 1 go/no-go: *would you publish this under your own
name?*

One such episode has already been generated and measured — 6 minutes, 46 turns,
two voices, on the free engine with a real LLM script. It is checked into
neither git nor the docs, but it is on disk at
`data/artifacts/6d038b2c-9282-4911-bd03-6460018631bf/episode.wav`, and
`docs/EXECUTION_RULES.md` §4 records what was measured from it. Nobody has
answered the go/no-go yet.

To run a worker instead of the in-process fallback:

```powershell
celery -A workers.orchestrator.celery_app worker -l info
```

---

## The whole stack in one command

Needs Docker Desktop. This is the configuration Gate 1 targets: Postgres instead
of SQLite (single-writer SQLite serialises concurrent runs), a real Celery
worker, and FFmpeg already in the image.

```bash
export MYVIDCRAFT_TTS_ENGINE=edge          # or openai, with a key
export MYVIDCRAFT_ANTHROPIC_API_KEY=sk-... # optional
docker compose up --build
```

- Web UI — <http://localhost:3000>
- API — <http://localhost:8000>

Migrations run as a one-shot `migrate` service, not from the API's entrypoint —
two API replicas would otherwise race to alter the same schema on every deploy.

## Regenerating without starting over

Once a run has finished, two operations re-render part of it cheaply:

- **Voice cast** — pin a different voice per speaker and re-render. Only TTS and
  stitch/master run again; the strategy, episode plan and dialogue are untouched.
- **Re-cut video** — toggle burned captions and b-roll slots. Reuses every WAV;
  only the compositor runs.

```powershell
# voice ids come from GET /voices, and differ per engine
curl -X POST "http://localhost:8000/runs/$RUN_ID/regenerate-voice" `
  -H "content-type: application/json" `
  -d '{"voice_overrides": {"Ada": "en-GB-SoniaNeural"}}'
```

An empty body re-renders with automatic casting.

## Tests

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
pytest
```

307 tests. They run against a temporary database and artifacts directory and
never call an LLM, a broker, a speech endpoint or FFmpeg. The FFmpeg encode and
mux tests skip themselves when FFmpeg is absent — CI installs it and **fails if
those tests skip**, so a green CI run never hides them.

CI (`.github/workflows/ci.yml`) additionally builds both images, runs Alembic
against a real Postgres, asserts the schema exists, and boots the API and the UI.

## Layout

| Path | What lives there |
|---|---|
| `apps/api/` | FastAPI app, models, settings, websocket |
| `apps/web/` | Next.js UI (single page) + its own Dockerfile |
| `workers/orchestrator/` | Celery tasks, stage graph, LLM wrapper, progress bus |
| `workers/media/tts/` | Speech engines behind the `SpeechEngine` protocol |
| `workers/media/audio/` | Stitching, loudness normalisation, MP3 export |
| `workers/media/video/` | Slideshow compositor, rasteriser, captions, mux |
| `packages/core/` | Stage definitions, schemas, shared types |
| `packages/prompts/` | Versioned prompt templates |
| `infra/migrations/` | Alembic revisions |
| `docs/` | Execution rules, architecture, plan, engineering review |

Start with [`docs/EXECUTION_RULES.md`](docs/EXECUTION_RULES.md) for what is being
built and why, then [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the seams.
[`docs/ENGINEERING_REVIEW.md`](docs/ENGINEERING_REVIEW.md) is an independent
review of the repo — deliberately unflattering, and the reason the gate ladder
exists.
