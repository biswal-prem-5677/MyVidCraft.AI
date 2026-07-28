# MyVidCraft.ai

Tier‑1 Autonomous AI Media Engine.

## Phase 1 (current target)

Working vertical slice:
- **Content Brain** → `StrategyPack`
- **Script engine** → schema-validated multi‑agent `DialogueTurn[]`
- **Async runs** with progress events + artifacts
- **Minimal UI** (added in Phase 1) to start a run and view outputs

## Local dev (Phase 1)

### Prereqs
- Python 3.11+ (you have 3.13)
- Redis

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

### Start web UI (Phase 1)

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

