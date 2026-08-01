#!/usr/bin/env python3
"""
Drive a running stack end to end and report whether Gate 1 is mechanically met.

Why this exists
---------------
Gate 1's remaining criterion is "`docker compose up` on Linux produces a
complete episode". That could not be checked on the machine this was developed
on — Docker is not installed there — so it stayed open, and an open criterion
that requires a human to remember a ten-step curl sequence tends to stay open.
This collapses it to one command.

It deliberately does NOT start the stack. Bringing up compose is one line the
operator can see the logs of; hiding it inside a script only makes a failure
harder to read. Start the stack, then point this at it.

It also does not answer the go/no-go. That question is "would you publish this
under your own name?", which is a judgment about the operator's reputation and
is not a thing a script gets to decide. This verifies the *mechanical* half —
that a complete, non-degenerate episode came out — and then hands over the file
to listen to.

Usage
-----
    docker compose up --build -d
    python scripts/verify_gate1.py
    python scripts/verify_gate1.py --base-url http://1.2.3.4:8000 --keep-going

Stdlib only, on purpose: it has to run against a remote deployment from a
machine that may have none of this project's dependencies installed.

Exit codes: 0 all checks passed, 1 a check failed, 2 could not reach the stack.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# A generated episode is several minutes of speech; a stall usually means a TTS
# rate-limit rather than a crash, so this is generous.
RUN_TIMEOUT_S = 45 * 60
HEALTH_TIMEOUT_S = 180
POLL_INTERVAL_S = 5

#: Below this, something rendered but it is not an episode — a couple of turns
#: that failed silently, or one turn repeated. 60s is the shortest thing worth
#: calling a result.
MIN_EPISODE_SECONDS = 60

#: A 24kHz mono 16-bit WAV runs ~48 KB/s, so a minute is ~2.8 MB. This is a
#: floor against a file that exists but is mostly header.
MIN_WAV_BYTES = 500_000

_PASS, _FAIL, _INFO = "PASS", "FAIL", "info"


class CheckFailed(Exception):
    """A verification step did not hold."""


class Reporter:
    """Accumulates results so one failure does not hide the checks after it."""

    def __init__(self, keep_going: bool) -> None:
        self.keep_going = keep_going
        self.failures: list[str] = []

    def ok(self, label: str, detail: str = "") -> None:
        print(f"  [{_PASS}] {label}{f' - {detail}' if detail else ''}")

    def info(self, label: str) -> None:
        print(f"  [{_INFO}] {label}")

    def fail(self, label: str, detail: str) -> None:
        print(f"  [{_FAIL}] {label} - {detail}")
        self.failures.append(f"{label}: {detail}")
        if not self.keep_going:
            raise CheckFailed(label)


def _request(url: str, method: str = "GET", payload: dict | None = None, timeout: float = 30.0):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"content-type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as res:  # noqa: S310 - operator-supplied URL
        body = res.read()
    return json.loads(body) if body else None


def wait_for_health(base: str, rep: Reporter) -> dict:
    """Poll /health until the API answers, so a slow first boot is not a failure."""
    print("\nStack readiness")
    deadline = time.time() + HEALTH_TIMEOUT_S
    last_error = "no attempt made"
    while time.time() < deadline:
        try:
            health = _request(f"{base}/health", timeout=10)
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            last_error = str(e)
            time.sleep(3)
            continue
        rep.ok("API reachable", f"{base}")
        return health
    print(f"  [{_FAIL}] API never became reachable at {base} - {last_error}")
    print("\nIs the stack up? Try `docker compose ps` and `docker compose logs api`.")
    sys.exit(2)


def check_health(health: dict, rep: Reporter) -> None:
    rep.info(
        "health: tts=%s video=%s ffmpeg=%s llm=%s broker=%s"
        % (
            health.get("tts_engine"),
            health.get("video_engine"),
            health.get("ffmpeg"),
            health.get("llm_enabled"),
            health.get("broker_reachable"),
        )
    )
    if health.get("status") != "ok":
        rep.fail("health status", f"expected ok, got {health.get('status')!r}")
    else:
        rep.ok("health status ok")

    # FFmpeg absence does not fail a run, but it silently downgrades the result
    # to "no mp3, no mp4" — which would make the episode checks below fail with a
    # much less obvious message than this one.
    if not health.get("ffmpeg"):
        rep.fail("ffmpeg present", "absent in the container; there will be no mp4 and no mp3")
    else:
        rep.ok("ffmpeg present")

    if health.get("tts_engine") == "edge" and not health.get("ffmpeg"):
        rep.fail("tts/ffmpeg pairing", "the edge engine requires ffmpeg to transcode each turn")

    if not health.get("video_ready"):
        rep.fail("video renderer ready", str(health.get("video_detail")))
    else:
        rep.ok("video renderer ready")

    if not health.get("llm_enabled"):
        rep.info("no LLM key — the script will be generic template filler (runs still complete)")


def run_episode(base: str, niche: str, rep: Reporter) -> str:
    print("\nGenerating an episode")
    project = _request(
        f"{base}/projects",
        "POST",
        {
            "name": "Gate 1 verification",
            "niche": niche,
            "brand_profile": {"tone": "confident, practical, no hype"},
            "defaults": {},
        },
    )
    run = _request(
        f"{base}/projects/{project['id']}/runs",
        "POST",
        {"mode": "teaching", "platforms": ["youtube"], "language": "en", "config": {}},
    )
    run_id = run["id"]
    rep.ok("run created", run_id)

    started = time.time()
    last_stage = None
    while time.time() - started < RUN_TIMEOUT_S:
        time.sleep(POLL_INTERVAL_S)
        state = _request(f"{base}/runs/{run_id}")
        stage, status = state.get("stage"), state.get("status")
        if stage != last_stage:
            print(f"        {stage} ({(state.get('progress') or 0) * 100:.0f}%)")
            last_stage = stage
        if status == "completed":
            rep.ok("run completed", f"{(time.time() - started) / 60:.1f} min")
            return run_id
        if status in {"failed", "cancelled"}:
            rep.fail(f"run {status}", str(state.get("error") or "no error recorded"))
            return run_id

    rep.fail("run timed out", f"still {last_stage} after {RUN_TIMEOUT_S // 60} min")
    return run_id


def check_artifacts(base: str, run_id: str, out_dir: Path, rep: Reporter) -> None:
    print("\nArtifacts")
    artifacts = _request(f"{base}/runs/{run_id}/artifacts") or []
    by_name = {Path(a.get("path", "")).name: a for a in artifacts}

    # The mp3 and mp4 are FFmpeg-dependent by design and degrade rather than
    # fail, so they are checked but reported distinctly from the WAV, which is
    # the artifact the go/no-go is actually about.
    for name in ("strategy_pack.json", "dialogue_turns.json", "episode.wav"):
        if name in by_name:
            rep.ok(f"{name} present")
        else:
            rep.fail(f"{name} present", "missing")

    for name in ("episode.mp3", "episode.mp4", "captions.srt"):
        if name in by_name:
            rep.ok(f"{name} present")
        else:
            rep.fail(f"{name} present", "missing — check ffmpeg in the image")

    turns = [n for n in by_name if n.startswith("turn_")]
    if len(turns) >= 2:
        rep.ok("per-turn audio", f"{len(turns)} turns")
    else:
        rep.fail("per-turn audio", f"expected several turns, found {len(turns)}")

    episode = by_name.get("episode.wav")
    if not episode:
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "episode.wav"
    url = f"{base}/artifacts/{episode['id']}/file"
    try:
        with urllib.request.urlopen(url, timeout=300) as res:  # noqa: S310
            target.write_bytes(res.read())
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        rep.fail("episode.wav downloadable", str(e))
        return

    size = target.stat().st_size
    if size < MIN_WAV_BYTES:
        rep.fail("episode.wav is substantial", f"only {size / 1024:.0f} KB")
    else:
        rep.ok("episode.wav downloaded", f"{size / 1_048_576:.1f} MB -> {target}")

    _check_wav_is_audible(target, rep)


def _check_wav_is_audible(path: Path, rep: Reporter) -> None:
    """
    Parse the WAV and confirm it is long enough and not pure silence.

    Size alone does not prove audio: a multi-megabyte file of zeroes is a
    perfectly valid, perfectly silent WAV, and that is exactly what a TTS
    backend returning empty bodies would produce.

    `array` rather than `audioop` because Python 3.13 removed the latter — the
    same reason `workers/media/audio/normalize.py` hand-rolls its metering.
    """
    import array
    import math
    import wave

    try:
        with wave.open(str(path), "rb") as w:
            channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
            frames = w.getnframes()
            raw = w.readframes(frames)
    except (OSError, wave.Error) as e:
        rep.fail("episode.wav parses", str(e))
        return

    seconds = frames / rate if rate else 0
    if seconds < MIN_EPISODE_SECONDS:
        rep.fail("episode is a real length", f"only {seconds:.1f}s")
    else:
        rep.ok("episode length", f"{int(seconds) // 60}m{int(seconds) % 60:02d}s @ {rate}Hz x{channels}")

    if width != 2:
        rep.info(f"sample width is {width} bytes; skipping the loudness check")
        return

    samples = array.array("h")
    usable = len(raw) - (len(raw) % samples.itemsize)
    samples.frombytes(raw[:usable])
    if sys.byteorder == "big":
        samples.byteswap()
    if not samples:
        rep.fail("episode contains audio", "no samples decoded")
        return

    # Mean square over the whole file. Speech with normal pauses lands far above
    # the floor; a silent or near-silent render lands at or near zero.
    total = 0
    for s in samples:
        total += s * s
    rms = math.sqrt(total / len(samples))
    dbfs = 20 * math.log10(rms / 32768.0) if rms > 0 else -999.0

    if dbfs < -60:
        rep.fail("episode is not silence", f"RMS {dbfs:.1f} dBFS — effectively silent")
    else:
        rep.ok("episode is audible", f"RMS {dbfs:.1f} dBFS")


def main() -> int:
    # A Windows console is cp1252 by default and cannot encode the punctuation in
    # this file's own help text, which aborts the script on a print rather than on
    # anything to do with the stack under test. Degrade the characters, not the run.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):  # not a real console; already fine
            pass

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:8000", help="API root (default: %(default)s)")
    parser.add_argument("--niche", default="AI productivity for solo founders")
    parser.add_argument("--out", default="./gate1-verification", help="where to save the episode to listen to")
    parser.add_argument("--keep-going", action="store_true", help="report every failure instead of stopping at the first")
    parser.add_argument(
        "--run-id",
        help="check an existing completed run instead of generating a new one. "
        "Re-checks a result without paying for another episode.",
    )
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    rep = Reporter(args.keep_going)

    print("=" * 68)
    print("Gate 1 mechanical verification")
    print("=" * 68)

    try:
        health = wait_for_health(base, rep)
        check_health(health, rep)
        if args.run_id:
            print(f"\nChecking existing run {args.run_id} (no new episode generated)")
            state = _request(f"{base}/runs/{args.run_id}")
            if state.get("status") != "completed":
                rep.fail("run completed", f"status is {state.get('status')!r}")
            else:
                rep.ok("run completed")
            run_id = args.run_id
        else:
            run_id = run_episode(base, args.niche, rep)
        check_artifacts(base, run_id, Path(args.out), rep)
    except CheckFailed as e:
        print(f"\nStopped at the first failure ({e}). Re-run with --keep-going to see them all.")

    print("\n" + "=" * 68)
    if rep.failures:
        print(f"RESULT: {len(rep.failures)} check(s) failed")
        for f in rep.failures:
            print(f"  - {f}")
        print("\nGate 1 is NOT mechanically met. Fix the above before the listen test.")
        return 1

    print("RESULT: all mechanical checks passed.")
    print(f"\nThe remaining half of Gate 1 is not automatable. Play {args.out}/episode.wav")
    print("and answer: would you publish this under your own name?")
    print("  yes -> record it in docs/EXECUTION_RULES.md and advance to Gate 2")
    print("  no  -> STOP. The rules say stop, and that is the point of having them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
