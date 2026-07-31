from __future__ import annotations

import logging
import wave
from pathlib import Path

from .normalize import DEFAULT_PEAK_CEILING_DBFS, DEFAULT_TARGET_DBFS, normalize_pcm16

log = logging.getLogger(__name__)


def _silence_frames(duration_ms: int, framerate: int, sampwidth: int, nchannels: int) -> bytes:
    if duration_ms <= 0:
        return b""
    frames = int((duration_ms / 1000.0) * framerate)
    frame_bytes = sampwidth * nchannels
    return b"\x00" * (frames * frame_bytes)


def stitch_turns_to_episode(
    turn_paths: list[str],
    pauses_before_ms: list[int],
    pauses_after_ms: list[int],
    out_wav_path: str,
    normalize: bool = True,
    target_dbfs: float = DEFAULT_TARGET_DBFS,
    peak_ceiling_dbfs: float = DEFAULT_PEAK_CEILING_DBFS,
) -> None:
    """
    Concatenate per-turn WAVs into one episode, inserting the pauses the script
    asked for.

    Each turn is loudness-normalized before it is written, so speakers that were
    synthesised with different voices or rates do not jump in volume mid-episode.
    Normalization applies only to 16-bit audio; anything else is passed through
    untouched rather than being corrupted.
    """
    if not (len(turn_paths) == len(pauses_before_ms) == len(pauses_after_ms)):
        raise ValueError("turn_paths and pause arrays must match length")
    if not turn_paths:
        raise ValueError("No turn audio provided")

    Path(out_wav_path).parent.mkdir(parents=True, exist_ok=True)

    with wave.open(turn_paths[0], "rb") as w0:
        params0 = w0.getparams()

    nchannels = params0.nchannels
    sampwidth = params0.sampwidth
    framerate = params0.framerate

    can_normalize = normalize and sampwidth == 2
    if normalize and not can_normalize:
        log.info("skipping normalization: sample width %s bytes is not 16-bit", sampwidth)

    with wave.open(out_wav_path, "wb") as out:
        out.setnchannels(nchannels)
        out.setsampwidth(sampwidth)
        out.setframerate(framerate)

        for p, pb, pa in zip(turn_paths, pauses_before_ms, pauses_after_ms, strict=True):
            out.writeframes(_silence_frames(pb, framerate, sampwidth, nchannels))
            with wave.open(p, "rb") as w:
                if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (nchannels, sampwidth, framerate):
                    raise ValueError(
                        f"WAV params mismatch for {p}: "
                        f"got {(w.getnchannels(), w.getsampwidth(), w.getframerate())}, "
                        f"expected {(nchannels, sampwidth, framerate)}"
                    )
                frames = w.readframes(w.getnframes())
            if can_normalize:
                frames = normalize_pcm16(frames, target_dbfs=target_dbfs, peak_ceiling_dbfs=peak_ceiling_dbfs)
            out.writeframes(frames)
            out.writeframes(_silence_frames(pa, framerate, sampwidth, nchannels))
