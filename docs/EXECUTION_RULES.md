# Execution Rules

The contract for how this project gets built. It exists because the failure mode
of a solo AI-assisted project is not bad code — it is a growing pile of
individually-good work that never becomes a usable thing.

**Read the Destination and the Gate Ladder before starting any work. If a task
does not move the current gate, it does not get done.**

---

## 1. The Destination

> **A logged-in user pays, describes a niche, and receives a finished episode
> they are willing to publish under their own name — on a machine that is not
> this laptop.**

That single sentence is the definition of done for the whole project. Every
milestone below is a piece of it. Nothing that fails to move that sentence
forward is in scope, regardless of how interesting it is.

Three words in it are load-bearing:

| Word | Means | Currently |
|---|---|---|
| **logged-in** | auth, accounts, ownership | ❌ does not exist |
| **willing to publish** | output quality clears a real bar | ❌ robotic voice, slideshow video |
| **not this laptop** | deployable, containerised, Linux | ❌ Windows-locked TTS |

Until all three are ✅, the project has no users, and work that does not target
them is decoration.

---

## 2. Non-Negotiable Rules

### R1 — Push every change

Every commit is pushed to GitHub the moment it is made. No batching, no "I'll
push when it's clean." A change that exists only on this machine does not exist.

### R2 — One gate at a time

Work only on the current gate in the Gate Ladder (§4). Do not start gate N+1
work because it is more interesting than gate N work. It always is.

### R3 — No new capability until the current one is reachable

A capability is not "built" until a user can reach it. Adding a second
unreachable capability is not progress; it is inventory.

**Test:** can a stranger with a browser use this? If no, do not add anything new.

### R4 — Nothing ships un-pushed, un-tested, or un-run

Every change: tests pass, the thing was actually executed once, and it is pushed.
"It should work" is not a status.

### R5 — Never check a box you have not earned

If a deliverable is partial, mark it partial and say exactly what is missing.
`IMPLEMENTATION_PLAN_TIER1.md:622` does this correctly for lip sync — that is the
standard. A green checklist that lies is worse than a red one that does not.

### R6 — Buy commodities, build only the differentiator

Auth, billing, TTS, monitoring, email, fonts — buy or import. The only things
worth hand-building here are `brain.py` (strategy) and the orchestrator. Every
hour spent hand-rolling a commodity is an hour not spent on the Destination.

**Precedent:** `raster.py` is 250 lines of hand-written bitmap font that cost the
project all non-Latin languages. Do not repeat that trade.

### R7 — Scope changes are written down, not remembered

Adding something to scope means editing §5 of this file in the same commit. If it
is not written here, it is not in scope, and "we discussed it" does not count.

### R8 — Every gate has a stop condition

Each gate below has a Go/No-Go. A No-Go means stop and reconsider the project,
not "push through anyway." The gates exist to make stopping possible.

---

## 3. Definition of Done

A unit of work is Done when **all six** are true. Not five.

| # | Criterion |
|---|---|
| 1 | It works, and was run at least once — not just tested |
| 2 | Tests cover it, and the whole suite is green |
| 3 | A user can reach it (UI or documented API) |
| 4 | It fails legibly — errors say what to do next |
| 5 | Docs updated: this file, the plan, and `ARCHITECTURE.md` if seams changed |
| 6 | Committed and **pushed** |

If any is false, the work is In Progress. There is no "done except for."

---

## 4. The Gate Ladder

Sequential. No skipping. Each gate ends in a decision, not just a deliverable.

### Gate 0 — Baseline ✅ COMPLETE

Pipeline runs end-to-end on one machine: niche → strategy → script → audio →
composed video, async with live progress.

*Delivered:* Phases 1–3, 248 tests, engine seams, artifact registry.
*Verified:* a 60s episode muxes to h264 720p25 + aac with matching duration.

---

### Gate 1 — Deployable 🔵 CURRENT — code complete, awaiting the listen test

**Objective:** the pipeline runs somewhere that is not this Windows laptop.

| Deliverable | Why | State |
|---|---|---|
| HTTP TTS backend registered via `SpeechEngine` | Removes the Windows/COM lock | ✅ `openai_tts.py` |
| Free TTS backend for the listen test | The go/no-go needed a funded account nobody had | ✅ `edge_tts.py`, opt-in |
| `pyttsx3` + `pywin32` deleted from requirements | The blocker itself | ✅ deleted |
| Dockerfile + docker-compose (api, worker, web, redis, postgres) | No deploy artifacts existed | ✅ written, **build unverified locally** |
| Postgres + **Alembic** migrations | SQLite is single-writer; no migrations = data loss on any schema change | ✅ + drift test |
| GitHub Actions CI running the suite | The suite ran only when remembered | ✅ 3 jobs |

**Success criteria:**

| Criterion | State |
|---|---|
| Two runs execute concurrently without locking | ✅ verified — `tests/test_concurrent_runs.py` |
| A complete episode renders on the free engine | ✅ verified 2026-08-01 — see the run record below |
| CI green on push | ⏳ unverified — repo is private, no `gh` on this machine |
| `docker compose up` on Linux produces a complete episode | ⏳ unverified — Docker not installed here; CI builds and boots both images, but does not render an episode |

**The verification run (2026-08-01).** Run `6d038b2c`, niche *AI productivity
for solo founders*, executed against the real API on the in-process fallback
(no broker), `MYVIDCRAFT_TTS_ENGINE=edge`, script stages on real
`claude-opus-5`. All five stages completed.

| Measured | Value |
|---|---|
| Episode | 5:59.8, 46 turns, two voices cast (Andrew + Ava) |
| `episode.wav` | 24 kHz mono `pcm_s16le`; −19.2 LUFS integrated, LRA 3.9 LU |
| `episode.mp4` | h264 1280×720 25fps + aac, duration matching the WAV exactly |
| Also produced | `episode.mp3`, `captions.srt`, storyboard, timeline, per-turn WAVs |
| Wall clock | 6.2 min for a 6.0 min episode — ≈1.03× realtime |
| Silence audit | one gap >1.5s across 46 turns (1.54s at 81.9s) — a beat, not a hole |

Two notes for whoever answers the go/no-go. The −19.2 LUFS is the documented
design, not a defect: `normalize.py` does RMS normalisation to −20 dBFS with a
true-peak ceiling and says so — it is deliberately not R128 gating. It is also
~3 LU quieter than the podcast convention of −16 LUFS, so judge the *voice*, not
the level. And the free engine held for 46 consecutive turns without a 403,
which is evidence it works, not evidence it is dependable — that is exactly why
it is not the shipping default.

**The go/no-go is now answerable without money.** It previously required an
OpenAI account with a payment method, which stalled the gate. `edge_tts.py`
registers the Edge read-aloud service as a second `SpeechEngine`: no key, no
account, and the same neural voices Edge uses for read-aloud — at least the
equal of the paid default, so a "yes" here cannot be a false positive from a
cheaper engine. It is **not** the default and must not be billed on: the
endpoint is undocumented and rate-limits without notice. Switching to the paid
engine is one setting, which is what the protocol seam was built for.

**Found while doing this work:** a real concurrency crash, not a test artifact.
`@celery_app.task` returns a `PromiseProxy` that resolves lazily and not
thread-safely, so two runs starting at once could leave one holding a half-bound
task and die in `push_request` with `AttributeError: 'NoneType' object has no
attribute 'push'`. Reachable in production via `_run_detached` whenever the
broker is down. Fixed by finalising the app at import.

**Go/No-Go — the honest one:**
> Generate one episode with the new TTS. **Listen to it.** Would you publish it
> under your own name?
>
> **No → STOP.** Do not proceed to Gate 2. The product's core output is not
> viable and no downstream work fixes that. One week spent learning this beats
> six months.

**The episode to judge now exists** —
`data/artifacts/6d038b2c-9282-4911-bd03-6460018631bf/episode.wav` (or
`.mp3`/`.mp4`). Everything mechanical is verified; the only thing standing
between this gate and a decision is a person playing that file. Per R5 the
answer is deliberately **not** recorded here yet, because nobody has listened.

**Risks:** hidden COM coupling (the `SpeechEngine` protocol should contain it —
this is what that abstraction was for); Postgres JSON column differences.

---

### Gate 2 — Usable by a Stranger ⚪ BLOCKED (needs Gate 1)

**Objective:** someone who is not you can sign up and use it.

| Deliverable | Why |
|---|---|
| Auth (Clerk or Supabase — **buy, per R6**) | No accounts exist |
| `owner` on Project + authz on every endpoint | Any caller can read any artifact today |
| ~~Fix CORS (`allow_origins=["*"]` + credentials is invalid)~~ | ✅ **done early** — see the amendment log |
| Rate limiting | No auth + no limits + LLM calls = unbounded cost exposure |
| Object storage (R2/S3) | Artifacts currently die with the container |
| Run history UI | `GET /runs` exists; the UI ignores it |
| Structured brand-profile form | Replaces the raw JSON textarea |

**Success:** 5 external testers complete signup → generate → download unaided,
and cannot see each other's artifacts.

**Go/No-Go:** did all 5 finish without you helping? If they got stuck at the same
place, fix that before proceeding.

---

### Gate 3 — Worth Paying For ⚪ BLOCKED

**Objective:** money changes hands and users come back.

| Deliverable | Why |
|---|---|
| Stripe billing (credit-based, not seats) | Cost scales with usage; pricing must too |
| `metrics` table — latency + cost per stage | Cannot price what you cannot measure |
| Default model → Sonnet/Haiku; Opus becomes a paid tier | ~10× cost reduction |
| Prompt caching | Shared system blocks are free money today |
| Surface `llm` vs `template` provenance | Stop billing for silent filler |
| Saved brand profiles / doctrine memory | The only retention mechanism available |

**Success:** unit cost per run is a known number. Users return unprompted.

**Go/No-Go — the business one:**
> Do 10+ users pay a **second** month?
>
> **No → the category is rejecting this.** Pivot or stop. Do not build Gate 4 to
> fix a retention problem; features do not fix retention.

---

### Gate 4 — Expand ⚪ BLOCKED

Only reachable with paying, retained users. This is where the original plan's
Phase 4 lives.

| Deliverable |
|---|
| Whisper transcript + word timestamps |
| Clip candidate scoring |
| 9:16 export with burned captions |
| Ranked shorts pack + download bundle |

**Go/No-Go:** does the clip pack measurably drive upgrades?

---

## 5. Scope Freeze

**Explicitly NOT being built.** Adding anything here requires editing this
section in the same commit as the work (R7), with a written reason.

| Item | Why not |
|---|---|
| Self-hosted lip sync (MuseTalk/LivePortrait) | GPU fleet + weights + CUDA ops. Buy via API if users ask. |
| Stable Diffusion B-roll | Second GPU pipeline before the first exists |
| Multilingual variants | Multiplies every cost before one language is proven |
| Auto-publish loop | Platform ToS liability — this ships the customers' ban mechanism |
| Trainable viral ranker | Needs labelled outcome data that will not exist for years. Heuristic stays. |
| YOLO / DeepSort tracking | No MVP justification whatsoever |
| Voice cloning | Requires a consent-capture flow and right-of-publicity review first |
| Teams / orgs / RBAC | Single-user until someone asks |
| GraphQL, vector DB, search | No problem in this product requires any of them |

---

## 6. How to Decide What Is Next

Run this in order. Stop at the first that applies.

1. **Is anything broken or failing?** → fix it first.
2. **Is anything committed but unpushed?** → push it (R1).
3. **Is there unfinished work in the current gate?** → finish it (R2).
4. **Is the current gate's Go/No-Go answerable?** → answer it, record the result.
5. **Gate passed?** → advance and update §7.
6. **Anything else** → it is out of scope. Check §5.

**If tempted by something interesting that is not the current gate:** write it in
§5 with a reason, then return to the gate. The idea is preserved; the focus is
not spent.

---

## 7. Status

| Gate | State | Blocking |
|---|---|---|
| 0 — Baseline | ✅ Complete | — |
| **1 — Deployable** | 🔵 **Episode rendered; awaiting the listen** | A person to play `episode.wav` and answer; and a Docker host |
| 2 — Usable by a stranger | ⚪ Blocked | Gate 1 go/no-go |
| 3 — Worth paying for | ⚪ Blocked | Gate 2 |
| 4 — Expand (clips) | ⚪ Blocked | Gate 3 |

**To close Gate 1.** The episode has been rendered — the steps below are only
needed to make another one. To answer the go/no-go, play the existing file:

```
data/artifacts/6d038b2c-9282-4911-bd03-6460018631bf/episode.wav
```

To render a fresh one (the container check still needs Docker, which is not
installed on this machine):

```bash
# Free path — no account, no card. FFmpeg required for this engine.
export MYVIDCRAFT_TTS_ENGINE=edge
# Paid path instead:
#   export MYVIDCRAFT_TTS_ENGINE=openai
#   export MYVIDCRAFT_OPENAI_API_KEY=sk-...
export MYVIDCRAFT_ANTHROPIC_API_KEY=sk-...   # optional; templates without it

docker compose up --build        # UI on :3000, API on :8000
# or locally: uvicorn apps.api.main:app --port 8000, then npm run dev in apps/web

curl -X POST localhost:8000/projects -H 'content-type: application/json' \
  -d '{"name":"Test","niche":"AI productivity","brand_profile":{"tone":"confident"},"defaults":{}}'
curl -X POST localhost:8000/projects/<id>/runs -H 'content-type: application/json' \
  -d '{"mode":"teaching","platforms":["youtube"],"language":"en","config":{}}'
curl localhost:8000/runs/<run_id>/artifacts     # then download episode.wav
```

Then answer the go/no-go: **would you publish that audio under your own name?**

**Destination check:**

| Requirement | State |
|---|---|
| logged-in | ❌ Gate 2 |
| willing to publish | ❌ Gate 1 go/no-go |
| not this laptop | ❌ Gate 1 |

**Open decisions** (recorded rather than assumed — see `ENGINEERING_REVIEW.md`
Appendix A):

1. ~~Which TTS vendor, and is there budget for paid API calls?~~ **Answered
   2026-08-01:** no budget for now. Verification runs on the free `edge` engine;
   the paid `openai` engine stays the default and the shipping target, and the
   switch is one setting whenever a key exists.
2. Is lip sync negotiable, or a hard product requirement?
3. Deployment target — Linux/container assumed; confirm.

---

## 8. Amendment Log

| Date | Change | Reason |
|---|---|---|
| 2026-08-01 | Created | Replace phase-ordering with gate-ordering, so scope is bounded by user-reachability rather than by feature ambition |
| 2026-08-01 | Added a second, free speech engine (`edge`) | The Gate 1 go/no-go required a funded API account, which blocked the gate indefinitely. R6 says buy commodities — but a commodity you cannot afford stalls the ladder, and the seam makes a second registration nearly free. Not the default, and explicitly not billable. |
| 2026-08-01 | Added the web UI to the container stack | Compose brought up an API with no way to reach it, which fails R3: a capability a stranger with a browser cannot use is not built. |
| 2026-08-01 | Fixed the CORS misconfiguration, out of gate order | A deliberate, narrow R2 deviation, recorded rather than quietly taken. `allow_origins=["*"]` with `allow_credentials=True` is not permissive-but-working, it is *invalid*, and Starlette resolves it by echoing any origin back — strictly worse than a wildcard. It was a Critical review finding (§8, A05), it is a correction to broken configuration rather than new capability (so R3 is untouched), and it is ~10 lines plus tests. Deferring a security defect behind an unrelated go/no-go had no upside. Gate 2 still does not start. |
| 2026-08-01 | Added `docs/HUMAN_TASKS.md` and `scripts/verify_gate1.py` | The gate kept stalling on tasks nobody had written down as needing a human. HUMAN_TASKS.md separates "blocked on a person" from "not built yet, correctly sequenced", so the difference between forgotten and decided is visible. The script collapses the Docker criterion to one command. |
| 2026-08-01 | Recorded the Gate 1 verification run | R4 says nothing ships un-run. The pipeline had been tested but never executed end to end on the free engine, so "code complete" was resting on the suite alone. It has now produced a real 6-minute episode; the measurements are in §4. The go/no-go answer is still absent on purpose (R5) — no one has listened yet. |
