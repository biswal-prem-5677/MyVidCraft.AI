# MyVidCraft.ai

Tier‑1 Autonomous AI Media Engine.

## Phase 2 (current target)

Working vertical slice — text in, mastered episode audio out:
- **Content Brain** → `StrategyPack`
- **Script engine** → schema-validated multi‑agent `DialogueTurn[]`
- **TTS** → one WAV per turn, with a distinct system voice per speaker
- **Audio master** → pauses from the script, per-turn loudness normalization → `episode.wav` (+ `episode.mp3` when FFmpeg is installed)
- **Async runs** with progress events + artifacts
- **Minimal UI** to start a run, preview per-turn and episode audio, and re-render the voices without regenerating the script

## Local dev

### Prereqs
- Python 3.11+ (you have 3.13)
- Redis — optional; without it runs execute in-process instead of on a worker
- FFmpeg — optional; without it runs still complete, just without the MP3

### Setup

```powershell
cd "c:\เอกสาร\MyVidCraft.ai"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Run Redis

If you already have Redis running, skip this. Otherwise run via Docker:

```powershell
docker run --name myvidcraft-redis -p 6379:6379 -d redis:7
```

### Start worker + API

```powershell
# terminal 1
cd "c:\เอกสาร\MyVidCraft.ai"
.\.venv\Scripts\Activate.ps1
celery -A workers.orchestrator.celery_app worker -l info

# terminal 2
cd "c:\เอกสาร\MyVidCraft.ai"
.\.venv\Scripts\Activate.ps1
uvicorn apps.api.main:app --reload --port 8000
```

### Start web UI

```powershell
# terminal 3
cd "c:\เอกสาร\MyVidCraft.ai\apps\web"
npm install
$env:NEXT_PUBLIC_API_BASE="http://localhost:8000"
npm run dev
```

Open:
- API docs: `http://localhost:8000/docs`
- Web UI: `http://localhost:3000`

`GET /health` reports whether the LLM, the Celery broker and FFmpeg are actually available.

## Regenerating voices without regenerating the script

Once a run has finished, the **Voice cast** card lets you pin a different system
voice per speaker and re-render. Only the TTS and stitch/master stages run again —
the strategy, episode plan and dialogue artifacts are left untouched, and the
previous audio artifacts are replaced rather than duplicated.

```powershell
# voice ids come from GET /voices
curl -X POST "http://localhost:8000/runs/$RUN_ID/regenerate-voice" `
  -H "content-type: application/json" `
  -d '{"voice_overrides": {"Ada": "TTS_MS_EN-US_ZIRA_11.0"}}'
```

An empty body re-renders with automatic casting.

## Tests

```powershell
cd "c:\เอกสาร\MyVidCraft.ai"
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
pytest
```

Tests run against a temporary database and artifacts directory, and never call an
LLM, a broker or the speech engine. The FFmpeg encode tests skip themselves when
FFmpeg is not on `PATH`.

