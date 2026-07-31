"""
MP3 export.

`ArtifactType.episode_audio_mp3` has existed in the schema since Phase 2 but
nothing ever produced it. MP3 encoding needs a real codec, so this shells out to
FFmpeg — which the Tier-1 plan already lists as a prerequisite.

FFmpeg is treated as optional, not required: if it is missing the WAV is still
produced and the run still completes, with the reason recorded on the run. That
keeps a machine without FFmpeg (like a fresh dev box) from failing every job.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_BITRATE = "192k"
_ENCODE_TIMEOUT_S = 300


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def ffmpeg_available() -> bool:
    return ffmpeg_path() is not None


def wav_to_mp3(wav_path: str, mp3_path: str, bitrate: str = DEFAULT_BITRATE) -> bool:
    """
    Transcode WAV -> MP3. Returns True on success, False if FFmpeg is unavailable
    or the encode failed. Never raises: MP3 is a nice-to-have artifact.
    """
    exe = ffmpeg_path()
    if exe is None:
        log.info("ffmpeg not on PATH; skipping mp3 export for %s", wav_path)
        return False

    source = Path(wav_path)
    if not source.exists():
        log.warning("cannot encode mp3, source missing: %s", wav_path)
        return False

    target = Path(mp3_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    cmd = [exe, "-y", "-loglevel", "error", "-i", str(source), "-codec:a", "libmp3lame", "-b:a", bitrate, str(target)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_ENCODE_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        log.warning("ffmpeg timed out encoding %s", wav_path)
        return False
    except OSError as e:
        log.warning("ffmpeg failed to start (%s)", e)
        return False

    if proc.returncode != 0:
        log.warning("ffmpeg exited %s: %s", proc.returncode, (proc.stderr or "").strip()[:500])
        return False
    if not target.exists() or target.stat().st_size == 0:
        log.warning("ffmpeg reported success but produced no output: %s", mp3_path)
        return False

    return True
