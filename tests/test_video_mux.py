"""
Muxing frames plus audio into an MP4.

The concat playlist carries the two details that made the first cut wrong, and
both are asserted here: entries are written as bare filenames (a Windows absolute
path needs the backslashes *and* the drive colon escaped in this format, and
getting it wrong fails at mux time), and the final image is repeated because the
concat demuxer ignores the duration of the last entry — without the repeat the
closing caption is dropped.

The encode itself skips when FFmpeg is absent, the same way `test_encode.py`
does. Everything above the subprocess boundary is tested unconditionally.
"""

from __future__ import annotations

import wave

import pytest

from workers.media.ffmpeg import ffmpeg_available
from workers.media.video.mux import CONCAT_FILENAME, mux_frames_to_mp4, write_concat_file
from workers.media.video.raster import Canvas


def _frame(path, color=(20, 30, 40), size=(64, 48)):
    return Canvas(size[0], size[1], color).write_ppm(path)


def _silence_wav(path, seconds=0.4, framerate=22050):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(framerate)
        w.writeframes(b"\x00\x00" * int(framerate * seconds))
    return path


# -- the concat playlist ----------------------------------------------------


def test_the_playlist_declares_the_ffconcat_version(tmp_path):
    path = write_concat_file(tmp_path, [(str(tmp_path / "scene_0000.ppm"), 1.0)])

    assert open(path, encoding="utf-8").readline().strip() == "ffconcat version 1.0"


def test_entries_are_bare_filenames_so_the_demuxer_resolves_them_relatively(tmp_path):
    """An absolute Windows path would need escaping this format does not do well."""
    frames = [(str(tmp_path / "frames" / "scene_0000.ppm"), 1.0)]

    text = open(write_concat_file(tmp_path, frames), encoding="utf-8").read()

    assert "file 'scene_0000.ppm'" in text
    assert str(tmp_path) not in text
    assert "\\" not in text


def test_each_entry_carries_its_own_duration(tmp_path):
    frames = [(str(tmp_path / "a.ppm"), 1.5), (str(tmp_path / "b.ppm"), 0.25)]

    lines = open(write_concat_file(tmp_path, frames), encoding="utf-8").read().splitlines()

    assert "duration 1.500" in lines
    assert "duration 0.250" in lines


def test_the_last_frame_is_repeated_so_it_is_not_dropped(tmp_path):
    """The concat demuxer ignores the final entry's duration."""
    frames = [(str(tmp_path / "a.ppm"), 1.0), (str(tmp_path / "b.ppm"), 2.0)]

    lines = open(write_concat_file(tmp_path, frames), encoding="utf-8").read().splitlines()

    assert lines.count("file 'b.ppm'") == 2
    assert lines[-1] == "file 'b.ppm'", "the repeat must be the closing entry"


def test_a_zero_duration_is_floored_to_something_the_demuxer_accepts(tmp_path):
    text = open(write_concat_file(tmp_path, [(str(tmp_path / "a.ppm"), 0.0)]), encoding="utf-8").read()

    assert "duration 0.000" not in text
    assert "duration 0.001" in text


def test_an_empty_playlist_is_still_a_valid_document(tmp_path):
    text = open(write_concat_file(tmp_path, []), encoding="utf-8").read()

    assert text.strip() == "ffconcat version 1.0"


def test_the_playlist_lands_beside_its_frames(tmp_path):
    target = tmp_path / "frames"

    path = write_concat_file(target, [(str(target / "a.ppm"), 1.0)])

    assert path.endswith(f"frames/{CONCAT_FILENAME}")
    assert (target / CONCAT_FILENAME).exists()


# -- muxing -----------------------------------------------------------------


@pytest.fixture
def pretend_ffmpeg(monkeypatch):
    """
    Make the binary look present without running it.

    `mux_frames_to_mp4` checks for FFmpeg before it validates its inputs, so on a
    machine without it the input checks below are unreachable otherwise.
    """
    from workers.media.video import mux

    monkeypatch.setattr(mux, "ffmpeg_path", lambda: "ffmpeg")


def test_a_missing_playlist_is_reported_not_raised(tmp_path, pretend_ffmpeg):
    """Video is the last stage: a problem here must not fail the whole run."""
    audio = _silence_wav(tmp_path / "episode.wav")

    ok, detail = mux_frames_to_mp4(str(tmp_path / "nope.txt"), str(audio), str(tmp_path / "out.mp4"))

    assert ok is False
    assert "playlist missing" in detail


def test_missing_audio_is_reported_not_raised(tmp_path, pretend_ffmpeg):
    playlist = write_concat_file(tmp_path, [(_frame(tmp_path / "a.ppm"), 1.0)])

    ok, detail = mux_frames_to_mp4(playlist, str(tmp_path / "nope.wav"), str(tmp_path / "out.mp4"))

    assert ok is False
    assert "audio missing" in detail


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed on this machine")
def test_frames_and_audio_mux_into_a_playable_mp4(tmp_path):
    frames = [(_frame(tmp_path / "a.ppm", (10, 20, 30)), 0.2), (_frame(tmp_path / "b.ppm", (40, 50, 60)), 0.2)]
    playlist = write_concat_file(tmp_path, frames)
    audio = _silence_wav(tmp_path / "episode.wav", seconds=0.4)
    out = tmp_path / "nested" / "episode.mp4"

    ok, detail = mux_frames_to_mp4(playlist, str(audio), str(out), fps=10)

    assert ok is True, detail
    assert out.exists() and out.stat().st_size > 0
    # ftyp is the first box of an MP4 container.
    assert b"ftyp" in out.read_bytes()[:64]


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed on this machine")
def test_a_corrupt_frame_fails_cleanly_with_the_reason(tmp_path):
    bad = tmp_path / "bad.ppm"
    bad.write_bytes(b"not an image")
    playlist = write_concat_file(tmp_path, [(str(bad), 0.2)])
    audio = _silence_wav(tmp_path / "episode.wav")

    ok, detail = mux_frames_to_mp4(playlist, str(audio), str(tmp_path / "out.mp4"))

    assert ok is False
    assert detail, "a failure must carry a diagnosable reason"


@pytest.mark.skipif(ffmpeg_available(), reason="ffmpeg is installed on this machine")
def test_without_ffmpeg_the_reason_names_the_missing_binary(tmp_path):
    playlist = write_concat_file(tmp_path, [(_frame(tmp_path / "a.ppm"), 1.0)])
    audio = _silence_wav(tmp_path / "episode.wav")

    ok, detail = mux_frames_to_mp4(playlist, str(audio), str(tmp_path / "out.mp4"))

    assert ok is False
    assert "ffmpeg not found" in detail
