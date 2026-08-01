"""
Locating FFmpeg, and the one conversion that is not optional.

Three engines now shell out to the same binary — the audio stage for MP3 export,
the video stage for muxing frames against the episode audio, and the edge speech
backend for the MP3->WAV transcode — so discovery lives in one place rather than
being defined three times with three chances to drift.

FFmpeg is optional in *most* places. A machine without it still completes a run;
it just gets fewer artifacts. `workers.media.audio.encode` re-exports these
names, which is where they originally lived.

`transcode_to_wav` is the exception, and it raises rather than returning False.
The distinction is deliberate: a missing MP3 is a run with one fewer artifact,
but a missing turn WAV is a hole in the episode audio, and the stitcher would
either refuse to run or silently produce a shorter episode. Callers that need a
turn rendered cannot degrade, so they must not be handed a boolean they might
ignore.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

_TRANSCODE_TIMEOUT_S = 120


class FFmpegError(RuntimeError):
    """FFmpeg was unavailable, failed, or produced nothing usable."""


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def ffmpeg_available() -> bool:
    return ffmpeg_path() is not None


def transcode_to_wav(
    source: str,
    target: str,
    *,
    sample_rate: int,
    channels: int = 1,
) -> None:
    """
    Convert any FFmpeg-readable audio file to 16-bit PCM WAV.

    Used by speech backends whose API returns a compressed format. The output
    parameters are forced rather than inherited, because `stitch_turns_to_episode`
    refuses to concatenate turns whose channel count, sample width or frame rate
    disagree — and an engine that let the source dictate them would produce turns
    that individually play fine and collectively fail to assemble.

    `pcm_s16le` is named explicitly: `.wav` alone does not pin a sample format,
    and a float-encoded WAV parses in `wave` but breaks the int16 arithmetic in
    `normalize_pcm16`.

    Raises `FFmpegError` on every failure path — see the module docstring for why
    this one does not degrade.
    """
    exe = ffmpeg_path()
    if exe is None:
        raise FFmpegError(
            "FFmpeg is required to convert this engine's audio to WAV but was not found on PATH. "
            "Install it (Windows: `winget install Gyan.FFmpeg`, Debian: `apt install ffmpeg`) "
            "and restart the API and worker, or switch MYVIDCRAFT_TTS_ENGINE to a backend that "
            "returns WAV directly."
        )

    src = Path(source)
    if not src.exists() or src.stat().st_size == 0:
        raise FFmpegError(f"nothing to transcode: {source} is missing or empty")

    dst = Path(target)
    dst.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        exe, "-y", "-loglevel", "error",
        "-i", str(src),
        "-ac", str(channels),
        "-ar", str(sample_rate),
        "-c:a", "pcm_s16le",
        str(dst),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_TRANSCODE_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired as e:
        raise FFmpegError(f"FFmpeg timed out converting {src.name} after {_TRANSCODE_TIMEOUT_S}s") from e
    except OSError as e:
        raise FFmpegError(f"FFmpeg failed to start: {e}") from e

    if proc.returncode != 0:
        raise FFmpegError(f"FFmpeg exited {proc.returncode} converting {src.name}: {(proc.stderr or '').strip()[:400]}")
    if not dst.exists() or dst.stat().st_size == 0:
        raise FFmpegError(f"FFmpeg reported success but wrote no audio for {src.name}")
