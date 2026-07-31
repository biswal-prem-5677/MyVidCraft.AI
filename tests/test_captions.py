"""
Caption segmentation and SRT export.

The load-bearing property here is that captions do not drift. Durations inside a
turn are apportioned by character count, and the whole point of accumulating from
a running cursor is that rounding cannot accumulate with it: every chunk starts
exactly where the previous one ended, and the last chunk lands exactly on the
turn's end. Those are the assertions worth keeping, because the compositor cuts
frames on these boundaries and the Phase 4 clip engine will trust them too.
"""

from __future__ import annotations

from workers.media.video.captions import (
    Caption,
    build_captions,
    chunk_lines,
    format_timestamp,
    to_srt,
    wrap_text,
)


def _turn(text: str, start: int, end: int, speaker: str = "Ada") -> dict:
    return {"speaker": speaker, "text": text, "start_ms": start, "end_ms": end}


# -- wrapping ---------------------------------------------------------------


def test_wrapping_never_exceeds_the_line_budget():
    lines = wrap_text("the quick brown fox jumps over the lazy dog", max_chars=12)

    assert lines
    assert all(len(line) <= 12 for line in lines), lines
    # Nothing may be dropped or duplicated on the way through.
    assert " ".join(lines) == "the quick brown fox jumps over the lazy dog"


def test_a_word_longer_than_the_line_is_hard_split():
    """Overflowing the frame is worse than breaking a word mid-way."""
    lines = wrap_text("hi ABCDEFGHIJ", max_chars=4)

    assert lines == ["hi", "ABCD", "EFGH", "IJ"]


def test_a_word_of_exactly_the_line_width_is_left_alone():
    assert wrap_text("ABCD", max_chars=4) == ["ABCD"]


def test_wrapping_collapses_whitespace_and_handles_empty_input():
    assert wrap_text("  spaced   out  ", max_chars=40) == ["spaced out"]
    assert wrap_text("", max_chars=40) == []


def test_a_zero_line_budget_degrades_instead_of_looping_forever():
    """max_chars is clamped to 1; a 0 would make the hard-split loop never end."""
    assert wrap_text("ab", max_chars=0) == ["a", "b"]


def test_chunking_groups_lines_into_caption_blocks():
    assert chunk_lines(["a", "b", "c", "d", "e"], max_lines=2) == [["a", "b"], ["c", "d"], ["e"]]
    assert chunk_lines([], max_lines=2) == []


# -- building captions from the turn timeline -------------------------------


def test_captions_cover_a_turn_exactly_end_to_end():
    """The first caption starts on the turn and the last ends on it, with no drift."""
    long_text = "one two three four five six seven eight nine ten eleven twelve"
    captions = build_captions([_turn(long_text, 1000, 9000)], max_chars=12, max_lines=1)

    assert len(captions) > 1, "the text should have been split into several captions"
    assert captions[0].start_ms == 1000
    assert captions[-1].end_ms == 9000


def test_captions_within_a_turn_are_exactly_contiguous():
    long_text = "one two three four five six seven eight nine ten eleven twelve"
    captions = build_captions([_turn(long_text, 1000, 9000)], max_chars=12, max_lines=1)

    for earlier, later in zip(captions, captions[1:]):
        assert earlier.end_ms == later.start_ms, "rounding drifted between chunks"


def test_captions_stay_ordered_across_several_turns():
    captions = build_captions(
        [_turn("first turn here", 0, 2000, "Ada"), _turn("second turn here", 2400, 5000, "Ben")],
        max_chars=8,
        max_lines=1,
    )

    starts = [c.start_ms for c in captions]
    assert starts == sorted(starts)
    assert {c.speaker for c in captions} == {"Ada", "Ben"}
    # A gap between turns is a real pause and must not be papered over.
    assert max(c.end_ms for c in captions if c.speaker == "Ada") == 2000
    assert min(c.start_ms for c in captions if c.speaker == "Ben") == 2400


def test_silent_turns_produce_no_captions():
    assert build_captions([_turn("", 0, 500), _turn("   ", 500, 900)]) == []


def test_captions_carry_the_speaker_through():
    captions = build_captions([_turn("short line", 0, 1000, "Ben")])

    assert [c.speaker for c in captions] == ["Ben"]
    assert captions[0].text == "short line"


def test_a_zero_length_turn_still_yields_a_caption():
    """A turn whose audio measured as empty must not vanish from the transcript."""
    captions = build_captions([_turn("blink", 4000, 4000)])

    assert len(captions) == 1
    assert captions[0].start_ms == captions[0].end_ms == 4000


def test_an_end_before_its_start_is_clamped_not_inverted():
    captions = build_captions([_turn("backwards", 5000, 1000)])

    assert all(c.end_ms >= c.start_ms for c in captions)


# -- SRT --------------------------------------------------------------------


def test_timestamps_use_srt_comma_milliseconds():
    assert format_timestamp(0) == "00:00:00,000"
    assert format_timestamp(1_234) == "00:00:01,234"
    assert format_timestamp(3_600_000) == "01:00:00,000"
    assert format_timestamp(3_661_001) == "01:01:01,001"
    # Never emit a negative cue time; players reject the whole file.
    assert format_timestamp(-5) == "00:00:00,000"


def test_srt_is_numbered_from_one_with_wrapped_lines_intact():
    captions = [
        Caption(start_ms=0, end_ms=1500, speaker="Ada", lines=["first line", "second line"]),
        Caption(start_ms=1500, end_ms=3000, speaker="Ben", lines=["reply"]),
    ]

    blocks = to_srt(captions).strip().split("\n\n")

    assert len(blocks) == 2
    assert blocks[0].startswith("1\n00:00:00,000 --> 00:00:01,500\nfirst line\nsecond line")
    assert blocks[1].startswith("2\n")


def test_a_too_short_cue_is_padded_up_to_readability():
    captions = [Caption(start_ms=0, end_ms=90, speaker="Ada", lines=["blink"])]

    assert "00:00:00,000 --> 00:00:00,700" in to_srt(captions, min_duration_ms=700)


def test_padding_never_overlaps_the_next_cue():
    """Overlapping cues render as flicker in most players."""
    captions = [
        Caption(start_ms=0, end_ms=90, speaker="Ada", lines=["blink"]),
        Caption(start_ms=200, end_ms=1200, speaker="Ada", lines=["then this"]),
    ]

    text = to_srt(captions, min_duration_ms=700)

    assert "00:00:00,000 --> 00:00:00,200" in text, "the first cue ran into the second"


def test_every_cue_is_visible_for_at_least_a_tick():
    """A zero-length cue is silently dropped by some players."""
    captions = [
        Caption(start_ms=1000, end_ms=1000, speaker="Ada", lines=["a"]),
        Caption(start_ms=1000, end_ms=2000, speaker="Ada", lines=["b"]),
    ]

    for line in to_srt(captions).splitlines():
        if "-->" in line:
            start, end = line.split(" --> ")
            assert end > start, line


def test_no_captions_is_an_empty_document_not_a_crash():
    assert to_srt([]) == ""


def test_a_caption_reports_its_own_span():
    cap = Caption(start_ms=500, end_ms=2000, speaker="Ada", lines=["two", "lines"])

    assert cap.duration_ms == 1500
    assert cap.text == "two lines"
