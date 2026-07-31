"""
Loudness normalization for 16-bit PCM.

Python 3.13 removed `audioop`, and pydub depends on it, so this is a small
dependency-free implementation over `array`.

This is RMS normalization with a true-peak ceiling, not full EBU R128 / LUFS
gating. It solves the actual Phase 2 defect — turns synthesised at different
rates and by different voices arrive at noticeably different loudness, so the
stitched episode jumps in volume between speakers. Swapping in real LUFS
metering later only changes `_measure`.
"""

from __future__ import annotations

import array
import math
import sys

# 16-bit signed full scale.
_FULL_SCALE = 32768.0
_INT16_MIN = -32768
_INT16_MAX = 32767

DEFAULT_TARGET_DBFS = -20.0
DEFAULT_PEAK_CEILING_DBFS = -1.0


def _to_int16_array(frames: bytes) -> array.array:
    samples = array.array("h")
    # `frombytes` requires a whole number of items; drop a trailing odd byte
    # rather than raising on a slightly truncated file.
    usable = len(frames) - (len(frames) % samples.itemsize)
    samples.frombytes(frames[:usable])
    if sys.byteorder == "big":
        samples.byteswap()  # WAV data is little-endian
    return samples


def _to_bytes(samples: array.array) -> bytes:
    if sys.byteorder == "big":
        samples = array.array("h", samples)
        samples.byteswap()
    return samples.tobytes()


def measure_dbfs(frames: bytes) -> tuple[float, float]:
    """Return (rms_dbfs, peak_dbfs). Silence reports -inf."""
    samples = _to_int16_array(frames)
    if not samples:
        return (-math.inf, -math.inf)

    peak = 0
    total = 0.0
    for s in samples:
        total += float(s) * float(s)
        a = -s if s < 0 else s
        if a > peak:
            peak = a

    rms = math.sqrt(total / len(samples))
    rms_dbfs = 20.0 * math.log10(rms / _FULL_SCALE) if rms > 0 else -math.inf
    peak_dbfs = 20.0 * math.log10(peak / _FULL_SCALE) if peak > 0 else -math.inf
    return (rms_dbfs, peak_dbfs)


def normalize_pcm16(
    frames: bytes,
    target_dbfs: float = DEFAULT_TARGET_DBFS,
    peak_ceiling_dbfs: float = DEFAULT_PEAK_CEILING_DBFS,
) -> bytes:
    """
    Scale `frames` toward `target_dbfs` RMS without letting the peak exceed
    `peak_ceiling_dbfs`. Digital silence is returned untouched.
    """
    samples = _to_int16_array(frames)
    if not samples:
        return frames

    rms_dbfs, peak_dbfs = measure_dbfs(frames)
    if rms_dbfs == -math.inf:
        return frames  # pure silence: no gain is meaningful

    gain = 10.0 ** ((target_dbfs - rms_dbfs) / 20.0)

    # Never push the loudest sample past the ceiling — clipping is worse than
    # being a little quiet.
    if peak_dbfs > -math.inf:
        max_gain = 10.0 ** ((peak_ceiling_dbfs - peak_dbfs) / 20.0)
        gain = min(gain, max_gain)

    if abs(gain - 1.0) < 1e-4:
        return frames

    out = array.array("h", bytes(len(samples) * 2))
    for i, s in enumerate(samples):
        v = int(round(s * gain))
        if v > _INT16_MAX:
            v = _INT16_MAX
        elif v < _INT16_MIN:
            v = _INT16_MIN
        out[i] = v

    return _to_bytes(out)
