"""
Muxing still frames plus episode audio into an MP4.

The compositor emits one frame per caption, not one per displayed frame: a
talking-head cut only changes when the caption or the speaker changes, so a
60-second episode is a few dozen images rather than 1,500. FFmpeg's concat
demuxer is what makes that work — it takes a playlist of images with explicit
per-image durations and expands them to a constant frame rate on the way out.

Encoding 1,500 near-identical full-resolution PPMs instead would cost gigabytes
of temporary disk for exactly the same output.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from ..ffmpeg import ffmpeg_path

log = logging.getLogger(__name__)

_MUX_TIMEOUT_S = 900
CONCAT_FILENAME = "frames.txt"


def write_concat_file(
    directory: str | Path,
    frames: list[tuple[str, float]],
    filename: str = CONCAT_FILENAME,
) -> str:
    """
    Write an FFmpeg concat playlist next to the frames it references.

    Entries are written as bare filenames and the playlist lives in the same
    directory, so the demuxer resolves them relatively. That sidesteps quoting a
    Windows absolute path — backslashes and the drive colon both need escaping in
    this format, and getting it wrong fails at mux time rather than here.

    The final image is repeated with no duration: the concat demuxer ignores the
    duration of the last entry, so without the repeat the closing frame is
    dropped and the video ends a caption early.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / filename

    lines = ["ffconcat version 1.0"]
    for name, seconds in frames:
        lines.append(f"file '{Path(name).name}'")
        lines.append(f"duration {max(0.001, seconds):.3f}")
    if frames:
        lines.append(f"file '{Path(frames[-1][0]).name}'")

    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(target).replace("\\", "/")


def mux_frames_to_mp4(
    concat_path: str,
    audio_path: str,
    out_path: str,
    fps: int = 25,
) -> tuple[bool, str]:
    """
    Encode the playlist against `audio_path`. Returns (ok, detail).

    Never raises: video is the last stage, and a codec problem should leave the
    run holding its audio and storyboard rather than marking it failed.
    """
    exe = ffmpeg_path()
    if exe is None:
        return False, "ffmpeg not found on PATH"
    if not Path(concat_path).exists():
        return False, f"frame playlist missing: {concat_path}"
    if not Path(audio_path).exists():
        return False, f"episode audio missing: {audio_path}"

    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        exe, "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(concat_path),
        "-i", str(audio_path),
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage",
        "-pix_fmt", "yuv420p", "-r", str(fps),
        "-c:a", "aac", "-b:a", "160k",
        # The video track is built from caption durations and the audio track is
        # authoritative; -shortest keeps a rounding difference between them from
        # leaving a frozen tail frame.
        "-shortest",
        "-movflags", "+faststart",
        str(target),
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_MUX_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        return False, "ffmpeg timed out muxing the episode video"
    except OSError as e:
        return False, f"ffmpeg failed to start ({e})"

    if proc.returncode != 0:
        detail = (proc.stderr or "").strip()[:500] or f"ffmpeg exited {proc.returncode}"
        log.warning("video mux failed: %s", detail)
        return False, detail
    if not target.exists() or target.stat().st_size == 0:
        return False, "ffmpeg reported success but produced no output"

    return True, "ok"
