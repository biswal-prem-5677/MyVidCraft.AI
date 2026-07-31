"""
Locating FFmpeg.

Two engines now shell out to the same binary — the audio stage for MP3 export and
the video stage for muxing frames against the episode audio — so discovery lives
in one place rather than being defined twice with two chances to drift.

FFmpeg stays optional everywhere it is used. A machine without it still completes
a run; it just gets fewer artifacts. `workers.media.audio.encode` re-exports these
names, which is where they originally lived.
"""

from __future__ import annotations

import shutil


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def ffmpeg_available() -> bool:
    return ffmpeg_path() is not None
