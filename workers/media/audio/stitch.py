from __future__ import annotations

import wave
from pathlib import Path


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
) -> None:
    """
    Pure-Python WAV stitcher (works on Python 3.13 without pydub/audioop).
    Assumes all input WAVs share the same params (channels, width, rate).
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

    with wave.open(out_wav_path, "wb") as out:
        out.setnchannels(nchannels)
        out.setsampwidth(sampwidth)
        out.setframerate(framerate)

        for p, pb, pa in zip(turn_paths, pauses_before_ms, pauses_after_ms, strict=True):
            out.writeframes(_silence_frames(pb, framerate, sampwidth, nchannels))
            with wave.open(p, "rb") as w:
                if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (nchannels, sampwidth, framerate):
                    raise ValueError(f"WAV params mismatch for {p}")
                out.writeframes(w.readframes(w.getnframes()))
            out.writeframes(_silence_frames(pa, framerate, sampwidth, nchannels))

