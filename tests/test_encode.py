import pytest

from workers.media.audio.encode import ffmpeg_available, wav_to_mp3


def test_missing_source_is_reported_not_raised(tmp_path):
    assert wav_to_mp3(str(tmp_path / "nope.wav"), str(tmp_path / "out.mp3")) is False


def test_ffmpeg_availability_is_a_bool():
    assert isinstance(ffmpeg_available(), bool)


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed on this machine")
def test_encodes_a_wav_to_mp3(tmp_path, make_wav):
    src = make_wav(tmp_path / "a.wav", seconds=0.3)
    out = tmp_path / "nested" / "a.mp3"

    assert wav_to_mp3(str(src), str(out)) is True
    assert out.exists() and out.stat().st_size > 0


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed on this machine")
def test_a_non_audio_source_fails_cleanly(tmp_path):
    junk = tmp_path / "junk.wav"
    junk.write_bytes(b"this is not audio")

    assert wav_to_mp3(str(junk), str(tmp_path / "out.mp3")) is False
