"""
Caption segmentation and SRT export.

These captions are derived from the turn timeline, not from speech recognition.
The stitcher knows exactly when each turn starts and ends, so a turn's caption
is frame-accurate at its boundaries; within a turn, the text is split into
readable chunks whose durations are apportioned by character count.

That approximation is deliberate and bounded: it is right at every turn edge and
drifts at most a chunk's width in the middle of a long turn. Word-level accuracy
needs forced alignment, which is the Phase 4 Whisper stage — at which point this
module keeps its shape and only `build_captions` changes its input.
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_MAX_CHARS_PER_LINE = 38
DEFAULT_MAX_LINES = 2

#: Never flash a caption for less than this; a 90ms subtitle is unreadable.
MIN_CAPTION_MS = 700


@dataclass(frozen=True)
class Caption:
    """One on-screen subtitle: a speaker, a time span, and 1-2 lines of text."""

    start_ms: int
    end_ms: int
    speaker: str
    lines: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(self.lines)

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


def wrap_text(text: str, max_chars: int = DEFAULT_MAX_CHARS_PER_LINE) -> list[str]:
    """
    Greedy word wrap. A single word longer than `max_chars` is hard-split rather
    than allowed to overflow the frame.
    """
    max_chars = max(1, max_chars)
    lines: list[str] = []
    current = ""

    for word in text.split():
        while len(word) > max_chars:
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:max_chars])
            word = word[max_chars:]
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= max_chars:
            current = f"{current} {word}"
        else:
            lines.append(current)
            current = word

    if current:
        lines.append(current)
    return lines


def chunk_lines(lines: list[str], max_lines: int = DEFAULT_MAX_LINES) -> list[list[str]]:
    """Group wrapped lines into per-caption blocks."""
    max_lines = max(1, max_lines)
    return [lines[i : i + max_lines] for i in range(0, len(lines), max_lines)]


def build_captions(
    turns: list[dict],
    *,
    max_chars: int = DEFAULT_MAX_CHARS_PER_LINE,
    max_lines: int = DEFAULT_MAX_LINES,
) -> list[Caption]:
    """
    Turn timeline entries into captions.

    Each `turn` needs `start_ms`, `end_ms`, `speaker` and `text`. Durations are
    apportioned across a turn's chunks by character count, accumulated from a
    running cursor so rounding cannot drift: chunk N+1 starts exactly where
    chunk N ended, and the last chunk lands exactly on the turn's end.
    """
    captions: list[Caption] = []

    for turn in turns:
        text = (turn.get("text") or "").strip()
        if not text:
            continue

        start = int(turn.get("start_ms", 0))
        end = int(turn.get("end_ms", start))
        speaker = str(turn.get("speaker") or "Speaker")
        span = max(0, end - start)

        chunks = chunk_lines(wrap_text(text, max_chars), max_lines)
        if not chunks:
            continue

        weights = [max(1, len(" ".join(c))) for c in chunks]
        total = sum(weights)

        cursor = 0
        for chunk, weight in zip(chunks, weights, strict=True):
            c_start = start + round(span * cursor / total)
            cursor += weight
            c_end = start + round(span * cursor / total)
            captions.append(Caption(start_ms=c_start, end_ms=c_end, speaker=speaker, lines=list(chunk)))

    return captions


def format_timestamp(ms: int) -> str:
    """SRT timestamp: HH:MM:SS,mmm."""
    ms = max(0, int(ms))
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    seconds, millis = divmod(ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def to_srt(captions: list[Caption], *, min_duration_ms: int = MIN_CAPTION_MS) -> str:
    """
    Render captions as an SRT document.

    A caption is padded up to `min_duration_ms` when the apportioned slice came
    out too short to read, but never past the start of the next one — an SRT with
    overlapping cues renders as flicker in most players.
    """
    blocks: list[str] = []

    for i, cap in enumerate(captions):
        end = max(cap.end_ms, cap.start_ms + min_duration_ms)
        if i + 1 < len(captions):
            end = min(end, max(cap.start_ms, captions[i + 1].start_ms))
        # A zero-length cue is dropped by some players; guarantee a visible tick.
        end = max(end, cap.start_ms + 1)

        body = "\n".join(cap.lines)
        blocks.append(f"{i + 1}\n{format_timestamp(cap.start_ms)} --> {format_timestamp(end)}\n{body}\n")

    return "\n".join(blocks)
