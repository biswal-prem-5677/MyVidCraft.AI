# Engineering Review Board — MyVidCraft.ai

**Review date:** 2026-08-01
**Review basis:** the actual repository at `main@4ae5b5f`, not the plan document. Where the plan and the code disagree, the code is cited.
**Reviewers:** Independent Engineering Review Board (CTO, Staff/Principal Engineers, SRE, DevOps, DBA, Security, QA, Product, UX, FinOps, Legal & Compliance)

**Codebase measured:** 8,401 LOC · 85 tracked files · 62 Python modules · 10 commits · 248 tests passing.

---

## Executive Summary — Read This First

The plan document sells *"Text → Multi-Platform Content Empire"*: autonomous AI influencers, lip-synced avatars, voice cloning, viral clip detection, multilingual publishing.

What is actually built is **a well-architected job orchestrator wrapped around two placeholder media backends**. Three findings dominate everything else in this review:

| # | Finding | Evidence | Severity |
|---|---|---|---|
| 1 | **The TTS engine is Windows-only and single-threaded.** It cannot run in a Linux container, which means it cannot be deployed as SaaS at all. | `requirements.txt:27` `pywin32; sys_platform == "win32"`; `pyttsx3_tts.py:31,112` process-wide `_synth_lock` serializes *all* synthesis | 🔴 Critical |
| 2 | **The headline feature does not exist.** No lip sync, no avatar. The output is a slideshow drawn with a 5×7 bitmap font. | `raster.py:27-28` `FONT_WIDTH=5, FONT_HEIGHT=7`; plan line 622 concedes `[~]` | 🔴 Critical |
| 3 | **There is no product.** No auth, no users, no tenancy, no billing, no deployment. `infra/` is an empty directory. | `main.py` has zero auth dependencies; `models.py` has no `owner`; `ls infra/` → empty; no `.github/workflows` | 🔴 Critical |

**The good news, stated honestly:** the orchestration core is genuinely above-average work — better than most seed-stage codebases. The engine seams (`SpeechEngine`, `VideoRenderer` protocols + registries), the derived progress model, the degradation rules, and the 248-test suite are real assets. That layer survives a rewrite of everything around it.

**The hard news:** the 20% that is intellectually interesting is built, and the 80% that determines whether anyone pays is deferred. The deferred 80% contains the two things the product is named for.

---

## 1. Product Feasibility

### Problem severity — moderate, and shrinking

The stated problem ("creating video content is slow and expensive") is real. But the specific solution — fully autonomous AI-generated podcast/talking-head content — is entering a category with a collapsing value floor.

**Facts, not opinion:**

- The sample output in `data/artifacts/` is templated filler: *"Stop scrolling. If you care about AI productivity, this will change how you think."* That is the deterministic fallback, but it is representative of the genre.
- YouTube's **Inauthentic Content Policy** (July 2025) explicitly demotes mass-produced repetitive content. TikTok and Meta both label and downrank synthetic media.
- The unit produced — a two-speaker AI podcast with robotic SAPI narration over a slideshow — is the exact artifact platforms are building classifiers to suppress.

### Existing alternatives

| Competitor | What they do | Why they beat you today |
|---|---|---|
| **HeyGen** | Real avatar + lip sync, 175+ languages | Actual product. ~$29–89/mo. Photorealistic. |
| **Synthesia** | Enterprise avatar video | SOC2, 230+ avatars, brand controls |
| **Opus Clip / Vizard** | Long-form → ranked shorts | This is your Phase 4, already shipped by others at $19/mo |
| **ElevenLabs** | Voice cloning + Studio | Voice quality unreachable with SAPI |
| **Descript** | Edit video like a doc | Owns the prosumer podcast workflow |

**Differentiation as built: none.** The compositor is a slideshow; the voice is a 1990s Windows API. Every axis on which you would compete is currently a placeholder.

**Differentiation as *planned*:** "autonomous strategy → publish loop." That is a genuinely under-served seam — competitors sell *tools*, not *decisions*. But it lives entirely in `brain.py`, which is currently heuristic templates plus one Opus call.

### Willingness to pay

| Segment | Pays? | Why / why not |
|---|---|---|
| Serious creators | ❌ | Brand risk. Will not ship robotic slideshows under their name. |
| Agencies | ❌ | Client-facing quality bar; needs review workflows |
| Affiliate/arbitrage spammers | ✅ | Real, paying, and *will churn the instant platforms clamp down* — plus they attract abuse, chargebacks, and platform bans |
| SMB / local business | 🟡 | Might pay for simple explainer video, but wants templates not "AI influencers" |

**The willingness-to-pay that exists today is concentrated in the segment you least want.** That is the central commercial problem, and no amount of engineering fixes it.

### Ratings

| Dimension | Score | Justification |
|---|---|---|
| **Technical Feasibility** | **7/10** | Orchestration is proven and working. Remaining pieces (MuseTalk, XTTS, Whisper) are known-hard but well-trodden. Docked for the Windows/GPU deployment gap. |
| **Commercial Feasibility** | **3/10** | Crowded, well-funded competition; no moat; buyer segment is adversarial to platforms |
| **User Value** | **3/10** | Current output is not publishable. Value is entirely prospective. |

---

## 2. Feature Review

| Feature | Status | Feasible | Complexity | Maint. | Value | Verdict | Ship in |
|---|---|---|---|---|---|---|---|
| Run orchestration + task graph | ✅ Built | High | Med | Low | High | ✅ **Ready** | V1 |
| Artifact registry + addressability | ✅ Built | High | Low | Low | High | ✅ **Ready** | V1 |
| Progress model (derived weights) | ✅ Built | High | Low | Low | Med | ✅ **Ready** | V1 |
| Strategy pack (Brain) | ✅ Built | High | Med | Med | **High** | ✅ **Ready** | V1 — *your only real differentiator* |
| Dialogue script engine | ✅ Built | High | Med | Med | High | ✅ **Ready** | V1 |
| LLM fallback to template | ✅ Built | High | Low | Low | Med | ⚠️ **Reconsider** | Silently shipping filler is worse than failing |
| TTS via pyttsx3/SAPI | ✅ Built | **Blocked** | Low | High | Low | ❌ **Remove** | Cannot deploy. Replace with API TTS. |
| Audio stitch + RMS normalize | ✅ Built | High | Med | Low | High | ✅ **Ready** | V1 |
| MP3 export (FFmpeg) | ✅ Built | High | Low | Low | Med | ✅ **Ready** | V1 |
| Slideshow compositor | ✅ Built | High | High | Med | **Low** | ⚠️ **Delay** | Impressive engineering, not a sellable output |
| 5×7 software rasterizer | ✅ Built | High | High | Med | **Very Low** | ❌ **Remove** | ASCII-only; Pillow does this better in 10 lines |
| Burned captions + SRT | ✅ Built | High | Med | Low | High | ✅ **Ready** | V1 |
| Regenerate voice / video | ✅ Built | High | Low | Low | High | ✅ **Ready** | V1 — genuinely good UX call |
| Retry policy + idempotency | ✅ Built | High | Low | Low | High | ✅ **Ready** | V1 |
| **Auth / users / tenancy** | ❌ **Missing** | High | Med | Low | **Critical** | 🔴 **BLOCKER** | V1 |
| **Billing** | ❌ Missing | High | Med | Med | Critical | 🔴 **BLOCKER** | V1 |
| **Deployment / infra** | ❌ Missing | High | Med | Med | Critical | 🔴 **BLOCKER** | V1 |
| **Metrics table** | ❌ Missing (planned) | High | Low | Low | High | ⚠️ **Delay** | V1.1 — you cannot price without it |
| Lip-synced avatar | ❌ Deferred | Med | **Very High** | **Very High** | High | ⚠️ **Delay** | V2 — buy, don't build |
| Whisper + clip engine (Ph 4) | ❌ Not started | High | High | Med | **High** | ⚠️ **Delay** | V2 — *highest-value deferred item* |
| B-roll via Stable Diffusion | ❌ Not started | Med | Very High | Very High | Med | ❌ **Remove from roadmap** | Future |
| Multilingual (Ph 5) | ❌ Not started | Med | High | High | Med | ❌ **Remove** | Future |
| Auto-publish loop (Ph 5) | ❌ Not started | Med | High | **Very High** | Med | ❌ **Never (as designed)** | Platform ToS liability |
| Viral score predictor | 🟡 Heuristic | Low | High | High | **Low** | ❌ **Remove the ML ambition** | Keep heuristic, drop "trainable ranker" |
| YOLO + DeepSort tracking | ❌ Not started | Med | Very High | Very High | Very Low | ❌ **Never** | No MVP justification |

### Removals with the strongest case

- **`raster.py` (the 5×7 font engine).** ~250 lines of hand-rolled bitmap glyph rendering to avoid a Pillow dependency. It cannot render non-Latin text at all — plan line 95 admits captions become `?`. That kills every non-English market on day one. Pillow is a 3MB wheel with full TrueType support. **This is the clearest case of engineering pride costing product capability in the entire repo.**
- **Auto-publish loop.** Automated posting of synthetic content at scale is precisely what platform ToS enforcement targets. You would be building your customers' ban mechanism and taking on the liability.

---

## 3. System Architecture Review

**Verdict: Good (core) / Needs Redesign (edges)**

This is the section where the codebase earns real credit.

### What is genuinely excellent

| Pattern | Location | Why it's right |
|---|---|---|
| **Engine seams via Protocol + registry** | `tts/registry.py`, `video/registry.py` | Factories registered, not instances — a GPU backend's heavy imports never load on a CPU box. Swapping backends is a registration, not an orchestrator change. Rare at this stage. |
| **Derived progress model** | `stages.py:58-97` | Stage weights → computed bounds. Adding a stage rebalances the bar automatically. Replaces 8 hand-tuned floats. Textbook. |
| **Degradation rules** | `encode.py:56`, `mux.py:102` | FFmpeg absent → run completes with reduced output instead of failing. Correct default for a media pipeline. |
| **Broker TCP probe** | `tasks.py:902-920` | Avoids the ~110s kombu retry budget on a dead Redis. Measured, documented, fixed. |
| **Circuit breaker on event bus** | `progress.py:24-51` | Telemetry can never stall a run. 0.25s timeouts + 30s mute. Correct. |
| **Timeline as engine hand-off** | `tasks.py:567-602` | Audio publishes real frame offsets; video consumes them rather than re-deriving. Prevents drift. |

### What needs redesign

| Component | Current | Problem | Required |
|---|---|---|---|
| **Frontend** | Single 600-line `page.tsx`, plain CSS | No routing, no run history page, no auth UI. Plan says shadcn; line 199 admits not adopted. | Decompose; add routes |
| **Auth** | **None** | `main.py` has zero auth. Any caller can read any artifact. | 🔴 Required |
| **Authorization** | **None** | No `owner` on `Project` despite plan line 472 specifying it | 🔴 Required |
| **CORS** | `allow_origins=["*"]` **+** `allow_credentials=True` | `main.py:52-58` — invalid combination; browsers reject it, and it signals no origin model | 🔴 Required |
| **Database** | SQLite, file-based | Single-writer. Concurrent runs will hit `database is locked`. | Postgres |
| **Storage** | Local filesystem | No S3/R2. Artifacts die with the container. | Object storage |
| **Queues** | One Celery queue | Plan line 511 specifies `cpu/gpu` split; line 109 admits not done | V2 |
| **Workers** | Detached daemon threads (`main.py:141-152`) | Fallback path runs multi-minute renders in API process threads. Sensible for dev; unacceptable in prod. | Enforce Celery |
| **Deployment** | **Nothing** | `infra/` empty; no Dockerfile, no compose, no CI | 🔴 Required |

### The architectural contradiction

`ARCHITECTURE.md` and the registry pattern exist specifically to make backends swappable. **That investment is sound.** But it was made *before* any real backend was registered. An excellent adapter layer was built and then two toys were plugged into it.

The seam is real and will pay off. The sequencing was wrong: it optimized for future flexibility while the present product had no sellable output.

---

## 4. Database Review

**Verdict: Adequate for a prototype. Not production-viable.**

| Aspect | State | Assessment |
|---|---|---|
| Schema | 3 tables: `Project`, `Run`, `Artifact` | Clean, minimal, correct shape |
| Relationships | String FKs, **no constraints** | `run_id: str = Field(index=True)` — no `foreign_key=`. Orphaned artifacts possible. |
| Indexes | `run.project_id`, `run.status`, `artifact.run_id`, `artifact.type` | Reasonable. **Missing:** composite `(run_id, type, path)` — now queried on every artifact write (`tasks.py:117-122`) |
| Normalization | JSON columns for config/metadata | Right call for evolving schemas |
| Read perf | Fine at current scale | `list_runs` caps at 200; no pagination cursor |
| Write perf | 🔴 **SQLite single-writer** | Every stage writes progress. Concurrent runs → lock contention. |
| **Migrations** | 🔴 **None** | No Alembic. `create_db_and_tables()` only. **Any schema change requires dropping the DB.** |
| **Backups** | 🔴 **None** | No strategy documented or implemented |
| Data integrity | 🟡 Improved | `_create_artifact` now idempotent — but enforced in *application code*, not a DB constraint |
| Missing table | 🔴 `metrics` | Plan line 475 specifies it; line 629 admits unbuilt. **You cannot price a product whose unit cost you don't measure.** |

### Top 3 database actions

1. **Alembic before any further schema work.** Every day this is deferred raises the migration cost.
2. **Postgres + `UNIQUE (run_id, type, path)`** — make idempotency a database guarantee, not a convention.
3. **Build the `metrics` table.** Cost-per-run is the single most important number for this business and it is currently uncomputable.

---

## 5. API Review

| Aspect | State | Assessment |
|---|---|---|
| Style | REST + WebSocket | Appropriate. No GraphQL needed. |
| **Versioning** | 🔴 None | No `/v1/` prefix. Add before any external consumer. |
| **Authentication** | 🔴 None | Every endpoint public |
| **Authorization** | 🔴 None | No ownership model |
| Pagination | 🟡 `limit` only | No cursor; fine for now |
| Filtering | 🔴 None | Cannot filter runs by project or status |
| **Rate limiting** | 🔴 None | A single caller can queue unbounded expensive runs |
| Error handling | ✅ Good | Correct codes; 409 on non-terminal rerun is a nice touch |
| **Documentation** | 🟡 Auto only | FastAPI `/docs`. No usage guide. |
| Consistency | ✅ Good | Predictable naming and shapes |
| Path traversal | ✅ **Guarded** | `_resolve_artifact_path` (`main.py:303-311`) correctly refuses paths outside artifacts dir |
| **Signed URLs** | 🔴 Missing | Plan line 455 promises them; artifacts served directly with no access control |

**The critical gap:** `GET /artifacts/{id}/file` serves *any* artifact to *any* caller with a UUID. There is no auth, no ownership check, no expiry. Once there are two customers, customer A can read customer B's content by guessing or leaking an ID. **This is a data breach waiting on a second user.**

---

## 6. AI System Review

| Aspect | Current | Assessment |
|---|---|---|
| **Model** | `claude-opus-5` (`settings.py:15`) | 🔴 **Wrong tier.** Opus for bulk content generation is the most expensive possible default. |
| Max tokens | 16,000 | High per call |
| Timeout | 240s | Reasonable for adaptive thinking, but 4min × N stages compounds |
| Prompts | Versioned `.md` in `packages/prompts/` | ✅ **Excellent practice.** Rare at this stage. |
| Schema enforcement | `client.messages.parse` + Pydantic | ✅ Correct — structured output, validated |
| **Fallback** | Deterministic template | ⚠️ **Dangerous** — see below |
| Caching | 🔴 None | No prompt caching. Strategy prompts share a large system block — free money left on the table. |
| **RAG** | Not used | ✅ **Correctly absent. Do not add it.** |
| **Vector DB** | Not used | ✅ **Correctly absent. Do not add one.** |
| Hallucination risk | Low-Med | Schema-constrained; risk is quality not validity |
| **Prompt injection** | 🔴 Unmitigated | `niche` and `brand_profile` flow into system prompts unsanitized (`brain.py:41-53`) |
| **Evaluation** | 🔴 None | No quality harness. No way to know if a prompt change made output worse. |
| Monitoring | 🔴 None | No token/cost logging |

### The fallback problem

`llm.py` is designed so *every* failure path returns `None` → template. The docstring calls it a design rule: *"this module never raises into the pipeline."*

For a demo, correct. **For a paid product, this is a silent-failure machine.** A customer pays, their API call rate-limits, and they receive generic filler *labeled as a successful run*. They will not notice until they have published it.

**Required fix:** record which path produced each artifact (`"generator": "llm" | "template"`) in artifact metadata, surface it in the UI, and never bill for a template run.

### Highest-ROI AI change

**Switch the default from Opus to Sonnet or Haiku and add prompt caching.** Content generation at volume is a throughput problem, not a frontier-reasoning problem. Keep Opus as an opt-in "high quality" tier that costs more — that is a pricing feature, not a default.

---

## 7. Infrastructure Review

**Verdict: Does not exist.**

| Component | State |
|---|---|
| Cloud architecture | 🔴 None |
| Hosting | 🔴 None |
| **Containers** | 🔴 No Dockerfile anywhere |
| **CI/CD** | 🔴 No `.github/workflows` — 248 tests run only when someone remembers |
| CDN | 🔴 None |
| Object storage | 🔴 Local filesystem only |
| Caching | 🔴 None |
| Queues | 🟡 Celery+Redis, dev-only, single queue |
| **Secrets** | 🟡 `.env` via pydantic-settings — fine locally, no vault |
| Environments | 🔴 No dev/staging/prod separation |
| Monitoring | 🔴 None |
| Logging | 🟡 Python `logging`, unstructured, no aggregation |
| Tracing | 🔴 None |
| Alerting | 🔴 None |
| Backups | 🔴 None |
| DR | 🔴 None |

**`infra/` is an empty directory.** The plan (lines 500-514) describes RunPod GPU workers, S3, CDN, and autoscaling queues. None of it is started.

### The deployment blocker, stated precisely

```
requirements.txt:27   pywin32>=306; sys_platform == "win32"
pyttsx3_tts.py:31     _synth_lock = threading.Lock()   # process-wide
pyttsx3_tts.py:112    with _synth_lock, _com_context():
```

The TTS engine requires **Windows COM**. It cannot run in a Linux container. And even on Windows, `_synth_lock` serializes *every* synthesis in the process — so one worker renders one turn at a time, globally.

**Consequences:**

- Cannot deploy to any standard container platform (ECS, Cloud Run, Fly, Render, Railway).
- Options are Windows VMs — expensive, poorly supported by orchestrators — or replacing the engine.
- Even on Windows, concurrency is 1 per process. Ten customers = ten serialized queues.

**This single dependency invalidates the entire hosting plan.** It must be resolved before infrastructure work begins, because it determines what infrastructure is even possible.

**Recommendation:** replace pyttsx3 with an HTTP TTS API (ElevenLabs / OpenAI / Azure / Deepgram). This deletes the COM code, the lock, the Windows pin, removes the concurrency ceiling, makes the whole thing containerizable, **and** dramatically improves voice quality — the worst output-quality axis. The `SpeechEngine` protocol means this is a new registration, not a refactor. **It is the highest-leverage change available in this codebase.**

---

## 8. Security Review

**Verdict: Prototype-grade. Not safe to expose to the internet.**

| Control | State | Severity |
|---|---|---|
| Authentication | 🔴 None | **Critical** |
| Authorization | 🔴 None | **Critical** |
| RBAC | 🔴 None | High |
| JWT / OAuth | 🔴 None | **Critical** |
| Encryption at rest | 🔴 None | High |
| Encryption in transit | 🔴 No TLS config | High |
| **CORS** | 🔴 `["*"]` + credentials | **Critical** |
| SQL injection | ✅ Protected | — (SQLModel/SQLAlchemy parameterized) |
| XSS | 🟡 React escapes by default | Low |
| CSRF | 🔴 N/A (no auth to forge) | Becomes critical *with* auth |
| **Rate limiting** | 🔴 None | **Critical** — unbounded cost exposure |
| Secrets | 🟡 `.env`, not committed | Medium |
| **Path traversal** | ✅ **Guarded** | — Genuinely well done |
| Audit logs | 🔴 None | High |
| Privacy / PII | 🟡 Minimal data collected | Medium |
| GDPR | 🔴 No deletion, export, or consent | High |
| SOC2 | 🔴 Not started | Medium (blocks enterprise) |

### OWASP Top 10 (2021)

| | Category | Status |
|---|---|---|
| A01 | Broken Access Control | 🔴 **No access control exists** |
| A02 | Cryptographic Failures | 🔴 No encryption |
| A03 | Injection | ✅ SQL safe / 🟡 prompt injection open |
| A04 | Insecure Design | 🟡 No threat model |
| A05 | Security Misconfiguration | 🔴 CORS wildcard + credentials |
| A06 | Vulnerable Components | ✅ **Actively managed** — advisory floors documented in `requirements.txt` |
| A07 | Auth Failures | 🔴 No auth |
| A08 | Integrity Failures | 🟡 No artifact signing |
| A09 | **Logging/Monitoring Failures** | 🔴 No security logging |
| A10 | SSRF | ✅ No user-controlled fetches |

**Credit where due:** A06 is handled better than most startups. `requirements.txt` carries explicit advisory floors (`starlette>=1.3.1`, `idna>=3.15`, `pydantic-settings>=2.14.2`) with comments explaining *why* each exists and when to drop it. The last two commits were dependency-advisory cleanup. That discipline is real.

**But:** dependency hygiene on an application with no authentication is polishing the locks on a house with no walls.

### Specific unbounded-cost exposure

No auth + no rate limiting + LLM calls on Opus + a `POST /projects/{id}/runs` endpoint = **anyone who finds the URL can burn the Anthropic budget in a loop.** This is not theoretical; scanners find open FastAPI `/docs` endpoints within hours of exposure.

---

## 9. Performance Review

| Dimension | Assessment | Evidence |
|---|---|---|
| Run kickoff | ✅ **<2s** — verified | TCP probe avoids kombu retry budget (`tasks.py:902`) |
| Video render | ✅ ~10s for 60s episode | Plan line 625, verified end-to-end |
| **TTS throughput** | 🔴 **Hard ceiling of 1** | `_synth_lock` is process-global |
| **DB writes** | 🔴 SQLite single-writer | Progress written per turn |
| Concurrency | 🔴 Effectively 1 run | Both above compound |
| Caching | 🔴 None anywhere | No prompt cache, no asset cache |
| Memory | ✅ Reasonable | PPM frames written to disk, not held |
| CPU | 🟡 Pure-Python rasterizer | Slow per frame, but frame count is bounded by caption count — a smart design choice |
| Cold starts | 🟡 N/A currently | Will matter with GPU workers |
| Background processing | ✅ Correct model | Celery + detached-thread fallback |

**The bottleneck is unambiguous:** TTS. Every other optimization is noise until the synthesis lock and its Windows binding are gone. Moving to an HTTP TTS API converts this from a serialized local resource into an N-concurrent network call — a 10–50× throughput change from one backend swap.

---

## 10. Cost Analysis

> **All figures are assumptions requiring validation.** Current vendor pricing has not been verified and should be before planning around these numbers. The *relative* magnitudes are the durable insight.

### Cost per run (60s episode, 2 speakers, ~5 turns)

| Component | Est. cost/run | Notes |
|---|---|---|
| LLM — **Opus 5, current default** | **$0.30 – $1.00** | 3 calls × up to 16k tokens. 🔴 Dominant cost. |
| LLM — Sonnet/Haiku alternative | $0.02 – $0.10 | **~10× cheaper** |
| TTS — pyttsx3 | $0 | Free, but unshippable |
| TTS — API (ElevenLabs-class) | $0.05 – $0.30 | Per 1k chars |
| Compute — render/mux | ~$0.01 | CPU-only, ~10s |
| Storage | ~$0.001/run/mo | Small artifacts |
| Bandwidth | ~$0.01 | Per download |
| **Total (current config)** | **~$0.35 – $1.05** | |
| **Total (optimized)** | **~$0.10 – $0.40** | |
| **With GPU lip sync (future)** | **$0.50 – $3.00+** | GPU-minutes dominate everything |

### Monthly infrastructure (100 active users, ~1,000 runs/mo)

| Item | Est. /mo |
|---|---|
| API hosting | $20 – $50 |
| Worker (CPU) | $40 – $100 |
| Postgres (managed) | $20 – $50 |
| Redis | $10 – $30 |
| Object storage + CDN | $10 – $40 |
| Monitoring (Sentry etc.) | $0 – $30 |
| Auth (Clerk/Auth0 tier) | $0 – $25 |
| Email | $0 – $20 |
| **Subtotal infra** | **$100 – $345** |
| **LLM+TTS @ 1,000 runs, optimized** | **$100 – $400** |
| **LLM+TTS @ 1,000 runs, current** | **$350 – $1,050** |
| **Total** | **$200 – $1,400** |
| **+ GPU worker if lip sync ships** | **+$300 – $1,500** |

### Development cost

| Scope | Eng-months | Est. cost (@$12k/mo loaded) |
|---|---|---|
| Ship *current* code as real SaaS | 3 – 5 | $36k – $60k |
| + Phase 4 (Whisper + clips) | +2 – 3 | +$24k – $36k |
| + Real lip sync | +3 – 6 | +$36k – $72k |
| **Full plan as written** | **12 – 20** | **$144k – $240k** |

### Expensive features, flagged

| Feature | Why expensive |
|---|---|
| 🔴 **Opus as default** | ~10× the necessary model cost, on every run |
| 🔴 **GPU lip sync** | Changes cost structure from cents to dollars per run *and* requires GPU ops expertise |
| 🔴 **Stable Diffusion B-roll** | Second GPU pipeline; a whole team's worth of tuning |
| 🟡 **Multilingual** | Multiplies TTS and render cost per language |

**Hidden costs nobody budgets:** content moderation (you are generating publishable media — abuse handling *will* be needed), chargebacks from the spam segment, platform-ban support load, and storage growth from users who never delete runs.

---

## 11. Implementation Estimation

**Scope estimated: minimum viable *commercial* product** — auth, billing, Postgres, containerized deploy, API TTS, hosted, with the existing pipeline. **Not** the full plan.

| Team | Duration | Notes |
|---|---|---|
| **1 engineer** | **4 – 6 months** | Serial. High bus-factor risk. |
| **3 engineers** | **2 – 3 months** | Best ratio — splits cleanly into backend/infra, frontend, media |
| **5 engineers** | **1.5 – 2.5 months** | Coordination overhead begins to bite |
| **10 engineers** | **1.5 – 2 months** | ❌ **Do not.** Brooks's Law. Codebase is 8k LOC; 10 engineers would collide constantly. |

### Assumptions

1. Scope is *commercial MVP*, not the Tier-1 plan.
2. TTS moves to an HTTP API (removes the Windows blocker).
3. Lip sync is **out of scope**.
4. Phase 4/5 **out of scope**.
5. Auth is bought (Clerk/Supabase), not built.
6. Billing is Stripe Checkout, not custom.
7. Engineers are competent full-stack; no ML research required.

### Schedule risks

| Risk | Impact | Mitigation |
|---|---|---|
| TTS migration reveals hidden coupling | +2–4 wks | The `SpeechEngine` protocol should contain this — *this is the payoff for that abstraction* |
| SQLite→Postgres without Alembic | +1–2 wks | Add migrations first |
| No CI → integration debt surfaces late | +2–3 wks | Set up CI in week 1 |
| Lip sync gets re-added mid-build | **+3 months** | 🔴 Freeze scope in writing |
| Solo-founder context loss | Unbounded | Document the seams (ARCHITECTURE.md already helps) |

---

## 12. Dependency Analysis

### Current (production)

| Dependency | Version | Risk | Note |
|---|---|---|---|
| fastapi | ≥0.115 | Low | Healthy |
| uvicorn | ≥0.30 | Low | |
| pydantic / -settings | ≥2.7 / ≥2.14.2 | Low | Advisory floor documented |
| sqlmodel | ≥0.0.22 | 🟡 Med | Pre-1.0, thin maintenance |
| celery | ≥5.4 | Low | Mature |
| redis | ≥5.0 | Low | |
| anthropic | ≥0.120 | Low | Optional at runtime |
| **pyttsx3** | ≥2.90 | 🔴 **Critical** | Unmaintained, Windows-bound |
| **pywin32** | ≥306 | 🔴 **Critical** | Platform lock-in |
| starlette / idna | ≥1.3.1 / ≥3.15 | Low | Transitive advisory floors |
| next / react | 15 / 19 | Low | Current |
| **FFmpeg** | External binary | 🟡 Med | Optional by design ✅ |

### Required but absent

| Category | Needed | Recommendation |
|---|---|---|
| **Auth** | 🔴 | Clerk or Supabase Auth — **buy** |
| **Payments** | 🔴 | Stripe |
| **Database** | 🔴 | Postgres + **Alembic** |
| **Object storage** | 🔴 | Cloudflare R2 (no egress fees) |
| **TTS** | 🔴 | ElevenLabs / OpenAI / Deepgram |
| **Monitoring** | 🔴 | Sentry |
| **Email** | 🔴 | Resend / Postmark |
| **CI** | 🔴 | GitHub Actions |
| **Rate limiting** | 🔴 | slowapi or gateway-level |
| Vector DB | — | ✅ **Not needed. Do not add.** |
| Search | — | ✅ **Not needed.** |

### Vendor lock-in

| Vendor | Lock-in | Assessment |
|---|---|---|
| Anthropic | 🟢 Low | Behind `llm.py` wrapper with fallback |
| pyttsx3 | 🟢 Low | Behind `SpeechEngine` protocol |
| FFmpeg | 🟢 Low | Isolated in `ffmpeg.py` |
| Celery/Redis | 🟡 Med | Standard, replaceable |

**The seam architecture has genuinely produced low vendor lock-in.** That is a real, earned asset — and it is exactly what makes the TTS replacement tractable rather than terrifying.

---

## 13. Production Readiness

| Requirement | State | Gap |
|---|---|---|
| Logging | 🟡 Partial | Unstructured, no aggregation |
| Monitoring | 🔴 None | No APM, no dashboards |
| Tracing | 🔴 None | |
| Health checks | ✅ **Good** | `/health` reports LLM, broker, FFmpeg, video readiness — genuinely useful |
| **Retry strategy** | ✅ **Good** | Scoped to `OSError`, bounded at 2, jittered backoff — verified firing |
| Timeouts | 🟡 Partial | LLM 240s, FFmpeg 300s; no global stage timeout |
| Error handling | ✅ Good | Errback wired; cancellation distinguished from failure |
| **Rollbacks** | 🔴 None | No deploy pipeline to roll back |
| Feature flags | 🔴 None | |
| **Env separation** | 🔴 None | |
| Secrets | 🟡 `.env` | No vault |
| **DB migrations** | 🔴 **None** | No Alembic — highest-urgency gap in this table |
| Backups | 🔴 None | |
| DR | 🔴 None | |
| Incident response | 🔴 None | |

**Production-ready: No.** Roughly 30% of the operational checklist is met. The parts that *are* met (health, retries, error handling, degradation) are the parts that require engineering judgment — which is encouraging. The parts missing are mostly the parts you buy or configure.

---

## 14. User Experience Review

| Stage | State | Friction |
|---|---|---|
| **Landing page** | 🔴 None | No marketing site, no value prop |
| **Signup** | 🔴 None | Cannot create an account |
| **Auth** | 🔴 None | |
| **Onboarding** | 🔴 None | User faces a raw form with a JSON textarea for brand profile |
| **First success** | 🟡 Works | Run → progress → artifacts. **But time-to-value is a robotic slideshow.** |
| **Daily usage** | 🔴 Broken | No run history page, no project list, no search |
| **Power user** | 🟡 Partial | Regenerate voice/video are genuinely good |
| **Upgrade** | 🔴 None | No billing |
| **Support** | 🔴 None | |
| **Account deletion** | 🔴 None | GDPR exposure |

### Highest-friction items

1. **The brand profile is a raw JSON textarea** (`page.tsx:26-28`). Users must hand-write `{"tone": "confident", ...}`. This is a developer console, not a product. → Replace with structured form fields.
2. **No run history.** Every reload loses context. Runs exist in the DB and `GET /runs` exists — the UI simply does not use it. → Cheapest high-value UI fix available.
3. **First success is disappointing.** The whole funnel delivers a slideshow with SAPI narration. **No UX polish compensates for that.** → This is an output-quality problem, not a design problem.

### What is genuinely well-designed

- Regenerate-voice and regenerate-video as *separate, cheap* operations is a sophisticated product call. Re-cutting the look without paying for another TTS pass is exactly the right cost-conscious affordance, and most teams miss it.
- Live progress with per-stage granularity (now including intra-stage updates) is the correct answer to a multi-minute pipeline.

---

## 15. Business Review

| Dimension | Assessment |
|---|---|
| **Pricing model** | 🔴 Undefined. No billing code. |
| **Recommended** | Credit-based (runs), not seats. Cost scales with usage, so pricing must too. |
| **Monetization** | Not started |
| **Retention** | 🔴 **Structurally weak** — no library, no history, no accumulated state. Nothing keeps a user. |
| **Expansion** | Volume tiers, extra languages, quality tier (Opus vs Haiku) |
| **Activation** | Undefined. Suggest: *first published video*, not first run. |
| **North Star** | **Videos actually published by users** — not runs generated. Runs measure your cost; publishes measure their value. |
| **MRR potential** | At $29/mo × 100 users = $2.9k MRR against $200–1,400 infra. Thin but survivable *if* CAC is near zero. |
| **LTV** | 🔴 Likely low. Expect 2–4 month churn in this category. LTV ≈ $60–120. |
| **CAC** | Unknown. Paid acquisition in the AI-video category is expensive — competitors are well-funded. **If CAC > $100, unit economics fail.** |
| **Positioning** | 🔴 Undifferentiated as built |

### The retention problem is the business problem

The product generates a file and hands it over. There is no library, no series continuity, no learning from performance, no reason to return next month rather than use a competitor.

**The one asset that could create retention is `brain.py`** — accumulated brand doctrine, topic history, and what performed well. That is the "content strategy memory" no competitor sells. It is also the cheapest thing in the entire plan to build, because it is text and database rows rather than GPUs.

**If this product is pursued, that is the thing to build.** Not lip sync.

---

## 16. Risk Register

| # | Risk | Category | Rank |
|---|---|---|---|
| 1 | **Windows-only TTS blocks all deployment** | Engineering | 🔴 **Critical** |
| 2 | **No auth — anyone can read any artifact** | Security | 🔴 **Critical** |
| 3 | **No rate limiting + no auth = unbounded LLM cost** | Operational | 🔴 **Critical** |
| 4 | **Core differentiator (lip sync) doesn't exist** | Business | 🔴 **Critical** |
| 5 | **No migrations — schema change = data loss** | Engineering | 🔴 **Critical** |
| 6 | **Output quality below publishable bar** | Business | 🔴 **Critical** |
| 7 | Platform policy shift kills the category | Business/Legal | 🔴 **Critical** |
| 8 | No deployment artifacts at all | Operational | 🟠 High |
| 9 | SQLite single-writer under concurrency | Scaling | 🟠 High |
| 10 | Silent LLM→template fallback ships filler as success | Product | 🟠 High |
| 11 | Opus default = ~10× cost | FinOps | 🟠 High |
| 12 | No CI — 248 tests run only manually | Maintenance | 🟠 High |
| 13 | No metrics table → cannot price | Business | 🟠 High |
| 14 | Voice cloning consent / likeness rights | Legal | 🟠 High |
| 15 | Generated content defamation/IP liability | Legal | 🟠 High |
| 16 | GDPR: no deletion or export | Compliance | 🟠 High |
| 17 | Solo bus factor | Maintenance | 🟠 High |
| 18 | Single Redis = SPOF for queue + events | Architecture | 🟡 Medium |
| 19 | Prompt injection via niche/brand fields | Security | 🟡 Medium |
| 20 | GPU ops expertise absent if lip sync ships | Operational | 🟡 Medium |
| 21 | ASCII-only font blocks non-English markets | Product | 🟡 Medium |
| 22 | sqlmodel pre-1.0 | Maintenance | 🟡 Medium |
| 23 | Technical debt in `page.tsx` monolith | Maintenance | 🟡 Medium |
| 24 | Anthropic vendor concentration | Vendor | 🟢 Low (well-abstracted) |

### The legal risks deserve more weight than founders usually give them

**Voice cloning (Phase 2/5):** Cloning a voice requires documented consent from the speaker. Several US states now have right-of-publicity statutes covering synthetic voice; the EU AI Act imposes transparency obligations on synthetic media. Shipping voice cloning without a consent-capture flow is one complaint away from a serious problem.

**Auto-publishing (Phase 5):** Automated posting of undisclosed synthetic content violates the disclosure requirements now in force on most major platforms. This would ship customers' ban mechanism.

**Generated content liability:** The Brain generates *"X is lying to you about Y"* templates (plan line 262). Applied to a named real person, that is a defamation vector originating in the product.

---

## 17. What Should NOT Be Built

### Remove entirely

| Item | Why |
|---|---|
| **`raster.py` 5×7 font engine** | ~250 lines of hand-rolled glyph rendering to avoid a 3MB Pillow dependency. Costs every non-Latin market. The dependency-free constraint is not worth a market. |
| **Trainable viral ranker** | Requires labeled outcome data that does not exist and will not for years. Keep the heuristic; delete the ML roadmap. |
| **YOLO + DeepSort tracking** | No MVP justification whatsoever. |
| **Stable Diffusion B-roll** | A second GPU pipeline before the first one exists. |
| **Auto-publish loop** | Platform ToS liability. Do not build the ban button. |
| **Multilingual (Phase 5)** | Multiplies every cost before anyone has proven they pay for one language. |

### Delay

| Item | Until |
|---|---|
| Lip-synced avatar | After paying users ask for it — **then buy it via API, don't self-host** |
| Whisper + clip engine | V2 — *but this is the highest-value deferred item* |
| CPU/GPU queue split | When a GPU stage actually exists |
| Music ducking / full LUFS | Nice-to-have; RMS+ceiling is adequate |

### On lip sync specifically

MuseTalk/LivePortrait self-hosted means: GPU fleet, model weights, VRAM management, cold starts, CUDA version hell, and a per-run cost of dollars rather than cents. For a pre-revenue team this is a multi-month detour with an ongoing ops burden.

**HeyGen and D-ID both sell avatar generation via API.** Buy it. If customers pay, demand is validated and self-hosting can be evaluated then. Building it first is the single most likely way this project dies with impressive code and no users.

---

## 18. MVP Recommendation

### The uncomfortable strategic question

Before the feature list — **the current product direction has weak fundamentals**: crowded market, no moat, platform headwinds, and a buyer segment you do not want. The strongest asset in this repo is not the media pipeline. It is:

1. A clean, well-tested async media-job orchestrator with swappable engine seams
2. `brain.py` — strategy generation, which nobody else sells as a product

**Three paths:**

| Path | Description | Assessment |
|---|---|---|
| **A. Narrow the product** | "AI podcast/audio episodes from a niche" — drop video entirely | ✅ **Recommended.** Audio is where the pipeline is genuinely strongest. Ship-quality is reachable in weeks with an API TTS swap. Competition is thinner. Platform risk far lower. |
| **B. Pivot to the Brain** | Sell content *strategy* — calendars, hooks, topic slates — no media generation | 🟡 Viable, cheaper, but a smaller market |
| **C. Continue as planned** | Full avatar video empire | 🔴 Not recommended without funding and a media-ML hire |

**Path A is what this board would approve.** It uses ~80% of what is built, removes the two blockers (Windows TTS via API swap; lip sync by deleting the requirement), and produces output that is genuinely publishable — an AI-generated podcast episode with good API voices is a *real product* in a way that an AI-generated slideshow is not.

### MVP feature set (Path A)

| Priority | Feature | Justification |
|---|---|---|
| **MUST** | Auth (Clerk/Supabase) | Cannot have users without it |
| **MUST** | `owner` on Project + authz checks | Cannot have two customers without it |
| **MUST** | **Replace pyttsx3 with API TTS** | Unblocks deployment AND fixes worst quality axis |
| **MUST** | Postgres + Alembic | SQLite will not survive concurrency |
| **MUST** | Dockerfile + one-command deploy | Cannot ship without it |
| **MUST** | Rate limiting | Cost protection |
| **MUST** | Object storage (R2) | Artifacts must outlive containers |
| **MUST** | Stripe billing | Cannot make money without it |
| **MUST** | CI running the 248 tests | Protect what is built |
| **MUST** | Run history UI | Basic usability |
| **MUST** | Switch default to Sonnet/Haiku | ~10× cost reduction |
| **SHOULD** | `metrics` table | Cannot price without unit cost |
| **SHOULD** | Structured brand-profile form | Remove the JSON textarea |
| **SHOULD** | Sentry | Know when it breaks |
| **SHOULD** | Surface `llm` vs `template` provenance | Stop shipping filler as success |
| **SHOULD** | Account deletion | GDPR |
| **NICE** | Keep video as "bonus" output | Already built; do not market it |
| **NICE** | Whisper transcripts | Cheap, useful, enables clips later |
| **FUTURE** | Clip engine | V2 — real value |
| **FUTURE** | Avatar via API | Only if customers ask |
| **LONG-TERM** | Brand memory / performance loop | The only real moat available |

---

## 19. Implementation Roadmap

### Phase 1 — Make It Deployable (3–4 weeks)

| | |
|---|---|
| **Objectives** | Remove the two hard blockers: Windows TTS and no-deployment |
| **Deliverables** | API TTS backend registered; pyttsx3 + pywin32 deleted; Dockerfile; docker-compose; GitHub Actions CI; Postgres + Alembic; R2 storage |
| **Dependencies** | TTS vendor account |
| **Success criteria** | `docker compose up` on Linux produces a complete episode; CI green on every push; concurrent runs succeed |
| **Go/No-Go** | 🔴 **If the TTS swap does not produce noticeably better audio than SAPI, stop and reconsider the whole product.** This is a cheap, decisive quality test — run it in week 1. |
| **Risks** | Hidden COM coupling (low — protocol should contain it); Postgres JSON column differences |

### Phase 2 — Make It a Product (4–6 weeks)

| | |
|---|---|
| **Objectives** | Convert working pipeline into something a stranger can pay for |
| **Deliverables** | Auth; `owner` + authz on every endpoint; rate limiting; Stripe; run history UI; structured brand form; Sentry; account deletion; **fix CORS** |
| **Dependencies** | Phase 1 |
| **Success criteria** | A stranger signs up, pays, generates, downloads — with zero founder involvement |
| **Go/No-Go** | Can 5 external beta users complete the flow unaided? |
| **Risks** | Scope creep into features; auth/authz retrofit touching every endpoint |

### Phase 3 — Make It Worth Paying For (4–6 weeks)

| | |
|---|---|
| **Objectives** | Close the quality gap; establish retention |
| **Deliverables** | `metrics` table + cost dashboard; LLM provenance surfaced; prompt caching; Whisper transcripts; **saved brand profiles / doctrine memory** |
| **Dependencies** | Phase 2 + real usage data |
| **Success criteria** | 30-day retention > 40%; unit cost known per run; users return without prompting |
| **Go/No-Go** | 🔴 **Do 10+ users pay a second month?** If not, the category is rejecting the product — pivot or stop. |
| **Risks** | Retention may simply not materialize — this is the real test |

### Phase 4 — Expand Only If Phase 3 Passed (6–10 weeks)

| | |
|---|---|
| **Objectives** | Clip engine — the highest-value deferred feature |
| **Deliverables** | Clip scoring; 9:16 export with captions; ranked shorts pack; download bundle |
| **Dependencies** | Phase 3 **passing its go/no-go** |
| **Success criteria** | Clip pack drives measurable upgrade conversion |
| **Go/No-Go** | Only proceed with paying, retained users |
| **Risks** | Opus Clip already owns this — a differentiated angle is needed, likely the strategy integration |

**Note what is absent from all four phases:** lip sync, B-roll generation, multilingual, auto-publish, trainable ranker. That is deliberate. Each is deferred until revenue justifies it.

---

## 20. Buildability Scorecard

| Dimension | Score | Justification |
|---|---|---|
| **Technical Feasibility** | **7/10** | Orchestration works end-to-end and is verified. Remaining work is known-hard, not unknown-hard. Docked for the Windows deployment blocker. |
| **Architecture Quality** | **8/10** | Genuinely strong: protocol seams, registries, derived progress, degradation rules, timeline hand-off. Docked for missing auth/tenancy layers and a monolithic frontend. **Highest score here, and earned.** |
| **Engineering Practicality** | **5/10** | Excellent judgment on hard problems, poor prioritization: a hand-rolled font engine exists while authentication does not. |
| **Maintainability** | **7/10** | 248 tests, exceptional comments explaining *why*, clean module boundaries, `ARCHITECTURE.md`. Docked for no CI and solo bus factor. |
| **Security** | **2/10** | No auth, no authz, no rate limiting, CORS wildcard with credentials. Credit for path-traversal guards and disciplined dependency-advisory management — but those are locks on a doorless house. |
| **Scalability** | **3/10** | Hard concurrency ceiling of 1 (synthesis lock). SQLite single-writer. No horizontal story. The *architecture* could scale; the *implementation* cannot. |
| **Operational Readiness** | **2/10** | Empty `infra/`, no CI, no monitoring, no migrations, no backups, no DR. Credit for a genuinely useful `/health` and correct retry semantics. |
| **Cost Efficiency** | **4/10** | Opus default is ~10× necessary. No caching. No metrics — spend is not even measurable. |
| **Commercial Viability** | **3/10** | Crowded market, no moat, output below publishable bar, structurally weak retention, adversarial buyer segment. |
| **MVP Readiness** | **3/10** | A working *pipeline*, not a working *product*. Cannot onboard a single paying user today. |
| **OVERALL** | **44/100** | |

**Read this score correctly.** 44/100 is not "bad code" — the code is above average for its stage. It is *"a strong engine with no car around it, in a market that may not want the car."* The 8/10 architecture and 2/10 security in the same repo is the whole story: excellent judgment applied to the wrong priorities.

---

## 21. Brutal Reality Check

### What is overengineered

**`raster.py`.** A 5×7 bitmap font engine, hand-written, to avoid depending on Pillow. It is skilled work. It is also the reason captions render non-Latin text as `?`, which forecloses every non-English market. Real engineering effort was spent to acquire a product limitation. Pillow would have been better in every dimension that matters to a user.

**The registry/protocol layer — with a caveat.** Two registries, two protocols, `ARCHITECTURE.md` documenting the seams — all built *before* a single real backend was registered. The abstraction is correct and it will pay off during the TTS swap. But it was built to make swapping easy while the thing being swapped in did not exist. Right pattern, wrong sequence.

**The retry/idempotency work.** Careful, well-tested, correctly scoped. Also: reliability engineering on a pipeline with no users, no auth, and no deployment. It made a well-built engine slightly better while the car remained unbuilt.

### What is unrealistic

**The 7-day build plan** (lines 569-607). "Day 5: Avatar video (lip sync). Integrate MuseTalk." Integrating MuseTalk in one day is not a plan; it is a wish. Phases 1–3 took 10 commits and produced real work — the honest schedule is months, not days.

**"Autonomous AI Media Engine."** Nothing here is autonomous. It generates artifacts when asked. The autonomy is entirely in Phase 5, which is unstarted and, in its auto-publish form, should stay that way.

**The MVP acceptance checklist.** Line 622 marks lip sync `[~]` with an honest note that it is "not honestly checkable." That honesty is commendable — but a checklist where the headline item is unachievable was aspirational, not a specification.

### What is technically impressive but commercially unnecessary

| Item | Impressive because | Unnecessary because |
|---|---|---|
| Pure-Python rasterizer | Dependency-free glyph rendering | Users judge output, not dependency count |
| Dependency-free RMS normalizer | Worked around `audioop` removal in 3.13 | A library would have been fine |
| One-frame-per-caption optimization | 60s episode = dozens of frames not 1,500 | Genuinely clever — but optimizing a render nobody watches |
| Advisory floor management | Better than most Series A companies | On an app with no authentication |

### What will likely fail first

**In order:**

1. **The first deployment attempt.** Someone tries to containerize it, `pywin32` fails to install on Linux, and the whole media pipeline is revealed as non-portable. This is a certainty, not a risk.
2. **The first two concurrent users.** SQLite locks and the synthesis mutex serialize everything. One user waits behind the other with no visibility into why.
3. **The first customer who watches the output.** SAPI narration over a slideshow does not survive contact with someone who expected the plan's description.
4. **The Anthropic bill**, if the API is ever exposed without rate limiting.

### Dangerous assumptions

| Assumption | Why it's dangerous |
|---|---|
| "The seam means backends are easy to swap" | True for *interface* shape. The TTS swap also changes async model, error semantics, cost model, and rate limits. The seam helps; it does not make it free. |
| "Local/free TTS keeps costs at zero" | It keeps costs at zero *and revenue at zero*, because it cannot be deployed |
| "Phases 4–5 will follow like 1–3 did" | Phases 1–3 were CPU-only Python. Phase 4 needs Whisper + ML scoring; Phase 5 needs GPUs and legal review. The difficulty curve is discontinuous. |
| "Fallback-to-template means runs always succeed" | It means runs always *appear* to succeed. Silent quality failure is worse than loud failure in a paid product. |
| "Architecture quality predicts product success" | It does not. It predicts *maintenance* cost. Plenty of beautifully architected products die unused. |

### What should be simplified

1. **Delete the video stage from the product narrative.** Keep the code; stop selling it. Ship audio.
2. **Delete `raster.py`.** Use Pillow if video is kept at all.
3. **Delete the Phase 5 roadmap.** Multilingual, auto-publish, brand cloning — all of it, until revenue exists.
4. **Collapse the model tiering.** Haiku/Sonnet default; Opus as a paid tier.
5. **Buy every commodity.** Auth, billing, TTS, monitoring, email. Build only `brain.py` and the orchestrator.

### What is genuinely excellent

Stated specifically, because it would be unfair to bury it:

- **The comments explain *why*, not *what*.** `tasks.py:59-88`, `stages.py:1-18`, `slideshow.py:1-23` are better technical writing than most production codebases contain. A new engineer could be productive here quickly.
- **Degradation rules are correct and consistent.** FFmpeg missing → reduced output, not failure. Redis missing → in-process fallback. LLM missing → template. One coherent philosophy, applied uniformly.
- **The broker TCP probe** (`tasks.py:902-920`) is exactly the kind of measured fix (110s → instant) that separates engineers who profile from engineers who guess.
- **`ARCHITECTURE.md` and the naming rule** — "name modules after the engine that owns them, never the phase that introduced them" — is a small discipline with a large long-term payoff.
- **Honesty in the plan document.** Line 622 refuses to check a box it has not earned. Line 95 admits the font limitation. Line 629 admits metrics are unbuilt. That intellectual honesty is rare and it is the main reason this review can be useful rather than archaeological.

**The engineering instincts here are good.** They have been aimed at the wrong targets.

---

## 22. Final Verdict

# 🟠 Requires Major Redesign

**But read the qualification, because it changes what happens Monday:**

The *core architecture does not need redesign*. The orchestrator, the engine seams, the progress model, and the test suite are sound and should be preserved intact. What requires redesign is the **product scope** and the **deployment substrate** — and those are large enough that "simplify" understates the work.

### Evidence supporting the verdict

| Finding | Evidence | Why it forces redesign |
|---|---|---|
| Media backends are undeployable | `requirements.txt:27` Windows pin; `pyttsx3_tts.py:31` global lock | Cannot containerize. Must replace, not simplify. |
| Headline feature absent | `raster.py:27-28`; plan line 622 `[~]` | The product's name-sake capability does not exist |
| No product layer | No auth, no `owner`, empty `infra/`, no CI | Not a gap — an entire missing tier |
| Cannot serve 2 users | SQLite + synthesis lock | Multi-tenancy requires substrate change |
| No migrations | No Alembic | Every future schema change destroys data |

### If this board were the CTO approving the project

We would **not** approve continued development on the plan as written. We would approve a **4-week de-risking sprint** with one decisive gate:

1. **Week 1:** Swap TTS to an HTTP API. Generate one episode. **Listen to it.**
2. **Week 1 gate:** Is the audio good enough to publish under your own name? — **If no, stop the project.** No amount of downstream work fixes an unpublishable core output, and this is learnable for one week of effort instead of six months.
3. **Weeks 2–4:** If yes — Docker, Postgres+Alembic, CI, auth, rate limiting. Then reassess.

That sequence costs one month and answers the only question that matters. Everything in this review is downstream of it.

### The honest summary

This is genuinely good code — better than the median startup codebase, with real judgment visible in the seams, the degradation rules, and the comments. That skill is not in question.

What is in question is **direction**. Six months of this quality of engineering aimed at auth, deployment, and output quality would have produced a shippable product. Aimed at bitmap fonts and retry semantics, it produced an excellent engine that no one can run.

The architecture built here is good enough to survive the pivot. Use it.

---

## Appendix A — Open Questions

These were not assumed, and they materially change the recommendation:

1. **Team size and runway.** Solo with savings vs. 3 engineers with funding are different verdicts.
2. **Target customer.** Has anyone who would pay been interviewed? What did they say?
3. **Is lip sync negotiable?** If it is a hard requirement, the timeline triples and a media-ML hire is needed.
4. **Deployment target.** Is Windows hosting acceptable, or is Linux/container required? (This review assumed the latter.)
5. **Budget for paid APIs.** If TTS/LLM spend must stay at zero, Path A is not viable and the answer changes to Path B.

---

## Appendix B — Evidence Index

Claims in this review trace to these locations in the repository at `main@4ae5b5f`:

| Claim | File / line |
|---|---|
| Windows-only TTS dependency | `requirements.txt:27` |
| Process-global synthesis lock | `workers/media/tts/pyttsx3_tts.py:31,112` |
| 5×7 ASCII-only bitmap font | `workers/media/video/raster.py:27-28,41,139` |
| No authentication on any endpoint | `apps/api/main.py` (no auth dependency present) |
| No ownership column | `apps/api/models.py:18-46` |
| CORS wildcard + credentials | `apps/api/main.py:52-58` |
| Path traversal guard (positive) | `apps/api/main.py:303-311` |
| Opus as default model | `apps/api/settings.py:15` |
| LLM never raises / always falls back | `workers/orchestrator/llm.py:1-14,105-125` |
| Derived progress model | `packages/core/myvidcraft_core/stages.py:58-97` |
| Broker TCP probe | `workers/orchestrator/tasks.py:902-920` |
| Event-bus circuit breaker | `workers/orchestrator/progress.py:24-51` |
| Idempotent artifact registration | `workers/orchestrator/tasks.py:102-129` |
| Retry policy scope | `workers/orchestrator/tasks.py:59-88` |
| FFmpeg failures swallowed by design | `workers/media/audio/encode.py:56`, `workers/media/video/mux.py:102` |
| Detached daemon-thread fallback | `apps/api/main.py:141-152` |
| Empty infra directory | `infra/` |
| No CI workflows | `.github/workflows` absent |
| Lip sync conceded incomplete | `docs/IMPLEMENTATION_PLAN_TIER1.md:622` |
| Non-Latin caption limitation conceded | `docs/IMPLEMENTATION_PLAN_TIER1.md:95` |
| Metrics table conceded unbuilt | `docs/IMPLEMENTATION_PLAN_TIER1.md:629` |
| Sample generated dialogue | `data/artifacts/acdb28fa-.../dialogue_turns.json` |
