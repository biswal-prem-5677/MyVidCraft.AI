import math

from workers.media.audio.normalize import (
    DEFAULT_PEAK_CEILING_DBFS,
    DEFAULT_TARGET_DBFS,
    measure_dbfs,
    normalize_pcm16,
)


def test_silence_measures_as_negative_infinity():
    assert measure_dbfs(b"\x00" * 200) == (-math.inf, -math.inf)


def test_empty_input_is_returned_untouched():
    assert normalize_pcm16(b"") == b""
    assert measure_dbfs(b"") == (-math.inf, -math.inf)


def test_silence_is_not_amplified():
    frames = b"\x00" * 200
    assert normalize_pcm16(frames) == frames


def test_quiet_audio_is_raised_to_the_target(pcm16_sine):
    # -40 dBFS RMS sine: rms = 32768 * 10**(-40/20), amplitude = rms * sqrt(2).
    amplitude = int(32768 * (10 ** (-40 / 20)) * math.sqrt(2))
    frames = pcm16_sine(0.2, 22050, amplitude)

    before, _ = measure_dbfs(frames)
    assert before < DEFAULT_TARGET_DBFS - 10  # precondition: genuinely quiet

    after, _ = measure_dbfs(normalize_pcm16(frames))
    assert abs(after - DEFAULT_TARGET_DBFS) < 0.5


def test_peak_ceiling_prevents_clipping():
    # A single full-scale spike in near-silence: chasing the RMS target would
    # demand a large gain, but the peak is already at 0 dBFS.
    frames = b"\x01\x00" * 500 + b"\xff\x7f" + b"\x01\x00" * 500

    _, peak_before = measure_dbfs(frames)
    assert peak_before > -0.1  # precondition: already at full scale

    _, peak_after = measure_dbfs(normalize_pcm16(frames))
    assert peak_after <= DEFAULT_PEAK_CEILING_DBFS + 0.05


def test_normalization_is_idempotent(pcm16_sine):
    # Re-running the stitcher over already-mastered turns must not creep the
    # level; a second pass should land on the same measurement as the first.
    frames = pcm16_sine(0.1, 22050, 2000)

    once, _ = measure_dbfs(normalize_pcm16(frames))
    twice, _ = measure_dbfs(normalize_pcm16(normalize_pcm16(frames)))

    assert abs(once - DEFAULT_TARGET_DBFS) < 0.1
    assert abs(twice - once) < 0.01


def test_trailing_odd_byte_does_not_raise(pcm16_sine):
    frames = pcm16_sine(0.05, 22050, 4000) + b"\x00"
    out = normalize_pcm16(frames)
    assert len(out) % 2 == 0  # the stray byte is dropped, not misread
