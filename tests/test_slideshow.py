"""
The shipped compositor.

Three invariants carry the render, and each one has a failure this file pins down:

- **Scenes are gapless.** A hole in the playlist freezes on whatever frame
  preceded it and desynchronises everything after, so a scene runs until the next
  one begins rather than until its own caption ends.
- **The layout cannot overlap itself.** Bands are measured from real glyph height
  and laid out bottom-up; advancing by round multiples of the layout unit is what
  made the first cut draw its captions through its own lower-third.
- **Frame dimensions are even.** yuv420p rejects an odd size at encode time with
  an error that says nothing about the cause.

Rendering here writes real PPMs but only muxes when FFmpeg is present, so the
drawing path is covered on every machine.
"""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from workers.media.ffmpeg import ffmpeg_available
from workers.media.video.base import EpisodeVideoSpec, SpeakerStyle
from workers.media.video.captions import build_captions
from workers.media.video.mux import CONCAT_FILENAME
from workers.media.video.slideshow import (
    ACCENTS,
    SlideshowRenderer,
    accent_for,
    build_scenes,
    build_speaker_styles,
    initials,
)

TURNS = [
    {"index": 0, "speaker": "Ada", "text": "Welcome back to the show.", "start_ms": 0, "end_ms": 2000,
     "broll_cues": ["a rising chart"]},
    {"index": 1, "speaker": "Ben", "text": "Glad to be here, thanks.", "start_ms": 2400, "end_ms": 4200,
     "broll_cues": []},
    {"index": 2, "speaker": "Ada", "text": "Let us get straight to it.", "start_ms": 4600, "end_ms": 6500,
     "broll_cues": ["a city timelapse", "hands on a keyboard"]},
]


def _spec(tmp_path: Path, turns=None, **overrides) -> EpisodeVideoSpec:
    turns = TURNS if turns is None else turns
    tmp_path.mkdir(parents=True, exist_ok=True)
    audio = tmp_path / "episode.wav"
    if not audio.exists():
        with wave.open(str(audio), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(22050)
            w.writeframes(b"\x00\x00" * int(22050 * 0.5))

    defaults = dict(
        run_id="run-1",
        title="An Episode About Testing",
        audio_path=str(audio),
        turns=turns,
        captions=build_captions(turns),
        speakers=build_speaker_styles(turns),
        out_dir=str(tmp_path),
        width=320,
        height=180,
        fps=10,
        duration_ms=6500,
    )
    defaults.update(overrides)
    return EpisodeVideoSpec(**defaults)


# -- speaker presentation ---------------------------------------------------


def test_a_speaker_keeps_the_same_accent_across_runs():
    """Accents are hashed from the name so the host looks the same every episode."""
    assert accent_for("Ada") == accent_for("Ada")
    assert accent_for("Ada") in ACCENTS


def test_different_speakers_are_told_apart_by_colour():
    assert accent_for("Ada") != accent_for("Ben")


def test_styles_are_built_once_per_speaker_with_roles_honoured():
    styles = build_speaker_styles(TURNS, roles={"Ada": "host", "Ben": "guest"})

    assert sorted(styles) == ["Ada", "Ben"]
    assert styles["Ada"].role == "host"
    assert styles["Ben"].role == "guest"


def test_an_uncast_speaker_defaults_to_host_rather_than_failing():
    assert build_speaker_styles(TURNS)["Ben"].role == "host"


def test_a_nameless_turn_still_gets_a_speaker():
    styles = build_speaker_styles([{"speaker": "", "text": "hi"}])

    assert "Speaker" in styles


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Ada Lovelace", "AL"),
        ("Ada", "AD"),
        ("ada_lovelace", "AL"),
        ("A", "A"),
        ("", "?"),
        ("   ", "?"),
    ],
)
def test_initials_are_drawn_for_the_avatar_plate(name, expected):
    assert initials(name) == expected


# -- scene construction -----------------------------------------------------


def test_scenes_are_gapless_so_the_video_covers_the_audio(tmp_path):
    """A gap would freeze on the preceding frame and desync everything after."""
    scenes = build_scenes(_spec(tmp_path))

    assert scenes[0]["start_ms"] == 0, "the first scene must start at zero"
    for earlier, later in zip(scenes, scenes[1:]):
        assert earlier["end_ms"] == later["start_ms"], "a gap opened in the playlist"


def test_the_last_scene_runs_to_the_end_of_the_audio(tmp_path):
    scenes = build_scenes(_spec(tmp_path, duration_ms=9000))

    assert scenes[-1]["end_ms"] == 9000


def test_a_duration_shorter_than_the_timeline_does_not_truncate_the_cut(tmp_path):
    """The timeline is authoritative; a bad duration must not drop a scene."""
    scenes = build_scenes(_spec(tmp_path, duration_ms=10))

    assert scenes[-1]["end_ms"] >= TURNS[-1]["end_ms"]


def test_every_scene_has_a_positive_length(tmp_path):
    scenes = build_scenes(_spec(tmp_path))

    assert all(s["end_ms"] > s["start_ms"] for s in scenes)


def test_scenes_are_indexed_in_order(tmp_path):
    scenes = build_scenes(_spec(tmp_path))

    assert [s["index"] for s in scenes] == list(range(len(scenes)))


def test_scenes_follow_the_captions_when_captions_are_on(tmp_path):
    spec = _spec(tmp_path)

    assert len(build_scenes(spec)) == len(spec.captions)


def test_scenes_fall_back_to_one_per_turn_when_captions_are_off(tmp_path):
    scenes = build_scenes(_spec(tmp_path, show_captions=False))

    assert len(scenes) == len(TURNS)
    assert [s["speaker"] for s in scenes] == ["Ada", "Ben", "Ada"]


def test_silent_turns_do_not_become_scenes(tmp_path):
    turns = [
        {"speaker": "Ada", "text": "", "start_ms": 0, "end_ms": 500},
        {"speaker": "Ada", "text": "Only this.", "start_ms": 500, "end_ms": 2000},
    ]
    scenes = build_scenes(_spec(tmp_path, turns=turns, captions=[], show_captions=False))

    assert len(scenes) == 1


def test_an_empty_timeline_produces_no_scenes(tmp_path):
    assert build_scenes(_spec(tmp_path, turns=[], captions=[])) == []


def test_a_scene_carries_the_broll_cues_of_the_turn_it_covers(tmp_path):
    """The cues come from the script; this is the seam's only current consumer."""
    scenes = build_scenes(_spec(tmp_path, show_captions=False))

    assert scenes[0]["broll_cues"] == ["a rising chart"]
    assert scenes[1]["broll_cues"] == []
    assert scenes[2]["broll_cues"] == ["a city timelapse", "hands on a keyboard"]


def test_scenes_are_ordered_even_if_the_timeline_is_not(tmp_path):
    turns = [
        {"speaker": "Ben", "text": "Second.", "start_ms": 3000, "end_ms": 4000},
        {"speaker": "Ada", "text": "First.", "start_ms": 0, "end_ms": 1000},
    ]
    scenes = build_scenes(_spec(tmp_path, turns=turns, captions=build_captions(turns)))

    assert [s["speaker"] for s in scenes] == ["Ada", "Ben"]


# -- rendering --------------------------------------------------------------


def test_rendering_writes_one_frame_per_scene(tmp_path):
    spec = _spec(tmp_path)

    result = SlideshowRenderer().render(spec)

    assert len(result.frame_paths) == len(result.scenes)
    assert all(Path(p).exists() for p in result.frame_paths)
    assert all(Path(p).read_bytes().startswith(b"P6") for p in result.frame_paths)


def test_frames_are_written_at_the_requested_size(tmp_path):
    result = SlideshowRenderer().render(_spec(tmp_path, width=320, height=180))

    assert Path(result.frame_paths[0]).read_bytes().startswith(b"P6\n320 180\n255\n")


def test_odd_dimensions_are_rounded_down_for_yuv420p(tmp_path):
    """An odd frame size fails at encode time with an unhelpful error."""
    result = SlideshowRenderer().render(_spec(tmp_path, width=321, height=181))

    assert Path(result.frame_paths[0]).read_bytes().startswith(b"P6\n320 180\n255\n")


def test_a_tiny_frame_is_floored_to_something_drawable(tmp_path):
    result = SlideshowRenderer().render(_spec(tmp_path, width=8, height=8))

    assert Path(result.frame_paths[0]).read_bytes().startswith(b"P6\n160 120\n255\n")


def test_frames_land_in_their_own_directory_with_a_playlist(tmp_path):
    SlideshowRenderer().render(_spec(tmp_path))

    frames = tmp_path / "frames"
    assert (frames / CONCAT_FILENAME).exists()
    assert sorted(p.name for p in frames.glob("*.ppm"))[0] == "scene_0000.ppm"


def test_scene_records_name_the_frame_that_was_drawn_for_them(tmp_path):
    result = SlideshowRenderer().render(_spec(tmp_path))

    for scene in result.scenes:
        assert Path(scene["frame"]).exists()
    # Storyboard paths are normalised so they read the same on every platform.
    assert all("\\" not in s["frame"] for s in result.scenes)


def test_an_empty_timeline_is_a_render_failure_not_a_blank_video(tmp_path):
    with pytest.raises(RuntimeError, match="timeline is empty"):
        SlideshowRenderer().render(_spec(tmp_path, turns=[], captions=[]))


def test_the_playlist_durations_add_up_to_the_episode(tmp_path):
    spec = _spec(tmp_path)

    SlideshowRenderer().render(spec)

    text = (tmp_path / "frames" / CONCAT_FILENAME).read_text(encoding="utf-8")
    total = sum(float(line.split()[1]) for line in text.splitlines() if line.startswith("duration"))
    assert total == pytest.approx(spec.duration_ms / 1000.0, abs=0.05)


def test_captions_and_broll_can_each_be_switched_off(tmp_path):
    """These are the knobs the re-cut endpoint exposes; both must reach the frame."""
    plain = SlideshowRenderer().render(
        _spec(tmp_path / "plain", show_captions=False, show_broll_slots=False)
    )
    full = SlideshowRenderer().render(_spec(tmp_path / "full"))

    assert Path(plain.frame_paths[0]).read_bytes() != Path(full.frame_paths[0]).read_bytes()


def test_a_speaker_missing_from_the_cast_still_renders(tmp_path):
    """`speakers` is a lookup, not a contract; a gap must not crash the render."""
    result = SlideshowRenderer().render(_spec(tmp_path, speakers={}))

    assert result.frame_paths


def test_a_long_title_and_long_cues_do_not_break_the_frame(tmp_path):
    turns = [
        {
            "speaker": "Ada",
            "text": "A very long line of dialogue that has to wrap several times over.",
            "start_ms": 0,
            "end_ms": 6000,
            "broll_cues": ["an extremely long b-roll cue description that will not fit in the slot panel"],
        }
    ]
    spec = _spec(
        tmp_path,
        turns=turns,
        captions=build_captions(turns),
        title="A Title So Long That It Cannot Possibly Fit Across The Frame " * 3,
    )

    result = SlideshowRenderer().render(spec)

    assert result.frame_paths
    assert Path(result.frame_paths[0]).read_bytes().startswith(b"P6\n320 180\n255\n")


def test_a_portrait_frame_lays_out_without_overflowing(tmp_path):
    """Phase 4 renders 9:16; the layout derives every band from the frame size."""
    result = SlideshowRenderer().render(_spec(tmp_path, width=270, height=480))

    assert Path(result.frame_paths[0]).read_bytes().startswith(b"P6\n270 480\n255\n")


def test_speaker_styles_reach_the_frame(tmp_path):
    """The active speaker is lit and the other dimmed — the turn-aware cut."""
    scenes = build_scenes(_spec(tmp_path, show_captions=False))
    renderer = SlideshowRenderer()
    spec = _spec(tmp_path, show_captions=False)

    ada = renderer._draw_scene(spec, scenes[0], 320, 180).to_ppm()
    ben = renderer._draw_scene(spec, scenes[1], 320, 180).to_ppm()

    assert ada != ben, "the cut does not change when the speaker does"


def test_a_speaker_style_default_is_used_when_the_cast_is_incomplete():
    style = SpeakerStyle(name="Unknown")

    assert style.role == "host"
    assert style.accent


# -- muxing (skipped without ffmpeg) ---------------------------------------


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed on this machine")
def test_a_full_render_produces_an_mp4(tmp_path):
    result = SlideshowRenderer().render(_spec(tmp_path))

    assert result.video_path, result.notes
    out = Path(result.video_path)
    assert out.exists() and out.stat().st_size > 0
    assert out.name == "episode.mp4"
    assert result.notes == []


@pytest.mark.skipif(ffmpeg_available(), reason="ffmpeg is installed on this machine")
def test_without_ffmpeg_the_storyboard_survives_and_the_reason_is_recorded(tmp_path):
    """A missing optional dependency degrades the run; it does not fail it."""
    result = SlideshowRenderer().render(_spec(tmp_path))

    assert result.video_path is None
    assert result.frame_paths, "the storyboard is still real output"
    assert any("ffmpeg" in note.lower() for note in result.notes)


def test_a_mux_failure_leaves_the_storyboard_and_explains_itself(tmp_path, monkeypatch):
    """Simulated so the degradation path is covered even where FFmpeg exists."""
    from workers.media.video import slideshow

    monkeypatch.setattr(slideshow, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(slideshow, "mux_frames_to_mp4", lambda *a, **k: (False, "codec exploded"))

    result = SlideshowRenderer().render(_spec(tmp_path))

    assert result.video_path is None
    assert result.frame_paths
    assert any("codec exploded" in note for note in result.notes)
