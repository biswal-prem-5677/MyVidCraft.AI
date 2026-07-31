import wave

import pytest

from workers.media.audio.stitch import stitch_turns_to_episode

FRAMERATE = 22050


def _frames(path):
    with wave.open(str(path), "rb") as w:
        return w.getnframes()


def _read(path):
    with wave.open(str(path), "rb") as w:
        return w.readframes(w.getnframes())


def test_episode_length_includes_the_requested_pauses(tmp_path, make_wav):
    a = make_wav(tmp_path / "a.wav", seconds=0.1)
    b = make_wav(tmp_path / "b.wav", seconds=0.1)
    out = tmp_path / "episode.wav"

    stitch_turns_to_episode([str(a), str(b)], [0, 200], [300, 0], str(out), normalize=False)

    silence = int(0.2 * FRAMERATE) + int(0.3 * FRAMERATE)
    assert _frames(out) == _frames(a) + _frames(b) + silence


def test_pass_through_audio_is_bit_identical_without_normalization(tmp_path, make_wav):
    a = make_wav(tmp_path / "a.wav", seconds=0.05)
    out = tmp_path / "episode.wav"

    stitch_turns_to_episode([str(a)], [0], [0], str(out), normalize=False)

    assert _read(out) == _read(a)


def test_normalization_changes_quiet_audio(tmp_path, make_wav):
    quiet = make_wav(tmp_path / "quiet.wav", seconds=0.05, amplitude=300)
    out = tmp_path / "episode.wav"

    stitch_turns_to_episode([str(quiet)], [0], [0], str(out), normalize=True)

    assert _read(out) != _read(quiet)


def test_mismatched_wav_params_are_rejected(tmp_path, make_wav):
    a = make_wav(tmp_path / "a.wav", framerate=22050)
    b = make_wav(tmp_path / "b.wav", framerate=16000)
    out = tmp_path / "episode.wav"

    with pytest.raises(ValueError, match="WAV params mismatch"):
        stitch_turns_to_episode([str(a), str(b)], [0, 0], [0, 0], str(out), normalize=False)


def test_pause_arrays_must_match_the_turn_count(tmp_path, make_wav):
    a = make_wav(tmp_path / "a.wav")
    with pytest.raises(ValueError, match="must match length"):
        stitch_turns_to_episode([str(a)], [0, 0], [0], str(tmp_path / "e.wav"))


def test_no_turns_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="No turn audio"):
        stitch_turns_to_episode([], [], [], str(tmp_path / "e.wav"))


def test_output_directory_is_created(tmp_path, make_wav):
    a = make_wav(tmp_path / "a.wav")
    out = tmp_path / "nested" / "deeper" / "episode.wav"

    stitch_turns_to_episode([str(a)], [0], [0], str(out), normalize=False)

    assert out.exists()


def test_non_16_bit_audio_skips_normalization_instead_of_corrupting_it(tmp_path, make_wav):
    a = make_wav(tmp_path / "a8.wav", sampwidth=1)
    out = tmp_path / "episode.wav"

    stitch_turns_to_episode([str(a)], [0], [0], str(out), normalize=True)

    assert _read(out) == _read(a)
