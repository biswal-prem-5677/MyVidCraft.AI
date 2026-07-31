"""
The software rasterizer.

Two properties matter more than pixel fidelity. First, every draw clips: the
buffer is one flat bytearray, so a rectangle that runs off the right edge would
otherwise wrap onto the next row and corrupt the frame instead of being trimmed.
Second, the output really is binary PPM, because FFmpeg reads these bytes with no
codec in between — a malformed header fails at mux time with an error that says
nothing about the cause.
"""

from __future__ import annotations

import pytest

from workers.media.video.raster import (
    FONT_ADVANCE,
    FONT_HEIGHT,
    Canvas,
    text_height,
    text_width,
)

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
RED = (255, 0, 0)


def _payload(canvas: Canvas) -> bytes:
    """The pixel bytes of `to_ppm`, with the header verified and stripped."""
    data = canvas.to_ppm()
    header = b"P6\n%d %d\n255\n" % (canvas.width, canvas.height)
    assert data.startswith(header), data[:20]
    return data[len(header) :]


def _pixel(canvas: Canvas, x: int, y: int) -> tuple[int, int, int]:
    buf = _payload(canvas)
    offset = (y * canvas.width + x) * 3
    return tuple(buf[offset : offset + 3])  # type: ignore[return-value]


# -- canvas basics ----------------------------------------------------------


def test_a_canvas_starts_filled_with_its_background():
    canvas = Canvas(4, 3, RED)

    assert _pixel(canvas, 0, 0) == RED
    assert _pixel(canvas, 3, 2) == RED


@pytest.mark.parametrize("size", [(0, 5), (5, 0), (-1, 5)])
def test_a_non_positive_canvas_is_rejected(size):
    with pytest.raises(ValueError, match="positive-sized"):
        Canvas(*size, BLACK)


def test_a_rectangle_fills_only_its_own_area():
    canvas = Canvas(6, 4, BLACK)

    canvas.fill_rect(1, 1, 2, 2, WHITE)

    assert _pixel(canvas, 1, 1) == WHITE
    assert _pixel(canvas, 2, 2) == WHITE
    assert _pixel(canvas, 0, 1) == BLACK
    assert _pixel(canvas, 3, 1) == BLACK
    assert _pixel(canvas, 1, 3) == BLACK


def test_a_rectangle_running_off_the_right_edge_does_not_wrap_onto_the_next_row():
    """The buffer is flat; unclipped writes would corrupt the following row."""
    canvas = Canvas(6, 4, BLACK)

    canvas.fill_rect(4, 1, 10, 1, WHITE)

    assert _pixel(canvas, 5, 1) == WHITE
    assert _pixel(canvas, 0, 2) == BLACK, "the fill wrapped around the row"


def test_a_rectangle_starting_off_canvas_is_clipped_to_the_visible_part():
    canvas = Canvas(6, 4, BLACK)

    canvas.fill_rect(-2, -2, 4, 4, WHITE)

    assert _pixel(canvas, 0, 0) == WHITE
    assert _pixel(canvas, 1, 1) == WHITE
    assert _pixel(canvas, 2, 2) == BLACK


@pytest.mark.parametrize(
    "args",
    [
        (0, 0, 0, 5),      # zero width
        (0, 0, 5, 0),      # zero height
        (20, 20, 5, 5),    # entirely below-right of the canvas
        (-20, 0, 5, 5),    # entirely left of the canvas
    ],
)
def test_a_rectangle_with_nothing_visible_is_a_no_op(args):
    canvas = Canvas(6, 4, BLACK)

    canvas.fill_rect(*args, WHITE)

    assert _payload(canvas) == bytes(BLACK) * 24


def test_alpha_blends_against_what_is_already_there():
    """This is what makes the caption band readable over a bright backdrop."""
    canvas = Canvas(2, 1, BLACK)

    canvas.fill_rect(0, 0, 2, 1, WHITE, alpha=0.5)

    assert _pixel(canvas, 0, 0) == (127, 127, 127)


def test_fully_transparent_draws_nothing_and_fully_opaque_replaces():
    canvas = Canvas(2, 1, BLACK)

    canvas.fill_rect(0, 0, 2, 1, WHITE, alpha=0.0)
    assert _pixel(canvas, 0, 0) == BLACK

    canvas.fill_rect(0, 0, 2, 1, WHITE, alpha=1.0)
    assert _pixel(canvas, 0, 0) == WHITE


# -- text -------------------------------------------------------------------


def test_text_metrics_account_for_the_inter_glyph_gap():
    # One glyph is 5 columns wide; each additional glyph adds a 1-column gap.
    assert text_width("A") == 5
    assert text_width("AB") == 5 + FONT_ADVANCE
    assert text_width("A", scale=3) == 15
    assert text_width("") == 0
    assert text_height() == FONT_HEIGHT
    assert text_height(scale=2) == FONT_HEIGHT * 2


def test_drawing_text_returns_the_advance_it_consumed():
    canvas = Canvas(80, 20, BLACK)

    advance = canvas.text(2, 2, "AB", WHITE, scale=2)

    assert advance == FONT_ADVANCE * 2 * 2


def test_drawing_text_marks_pixels_inside_its_own_box_only():
    canvas = Canvas(40, 20, BLACK)

    canvas.text(4, 5, "H", WHITE, scale=1)

    painted = [
        (x, y)
        for y in range(20)
        for x in range(40)
        if _pixel(canvas, x, y) == WHITE
    ]
    assert painted, "nothing was drawn"
    assert all(4 <= x < 4 + 5 for x, _ in painted)
    assert all(5 <= y < 5 + FONT_HEIGHT for _, y in painted)


def test_a_space_draws_nothing():
    canvas = Canvas(20, 12, BLACK)

    canvas.text(0, 0, " ", WHITE)

    assert _payload(canvas) == bytes(BLACK) * (20 * 12)


def test_an_uncovered_character_renders_as_the_substitute_glyph():
    """The font is ASCII-only; non-Latin captions need a real font stack."""
    substitute = Canvas(20, 12, BLACK)
    substitute.text(1, 1, "?", WHITE)

    thai = Canvas(20, 12, BLACK)
    thai.text(1, 1, "ก", WHITE)

    assert _payload(thai) == _payload(substitute)


def test_scaling_text_makes_every_lit_pixel_a_block():
    small = Canvas(20, 12, BLACK)
    small.text(0, 0, "L", WHITE, scale=1)
    lit_small = sum(1 for y in range(12) for x in range(20) if _pixel(small, x, y) == WHITE)

    big = Canvas(40, 24, BLACK)
    big.text(0, 0, "L", WHITE, scale=2)
    lit_big = sum(1 for y in range(24) for x in range(40) if _pixel(big, x, y) == WHITE)

    assert lit_big == lit_small * 4


def test_a_scale_below_one_is_clamped_rather_than_drawing_nothing():
    canvas = Canvas(20, 12, BLACK)

    canvas.text(0, 0, "L", WHITE, scale=0)

    assert any(_pixel(canvas, x, y) == WHITE for y in range(12) for x in range(20))


def test_centred_text_is_symmetric_about_the_given_axis():
    canvas = Canvas(41, 12, BLACK)

    canvas.text_centered(20, 2, "AA", WHITE)

    columns = [x for x in range(41) for y in range(12) if _pixel(canvas, x, y) == WHITE]
    assert columns
    assert abs((min(columns) + max(columns)) // 2 - 20) <= 1


def test_text_running_past_the_edge_is_clipped_not_wrapped():
    canvas = Canvas(12, 20, BLACK)

    canvas.text(8, 0, "WWWW", WHITE)

    # Row 8 is below the glyph box entirely; a wrap would have painted it.
    assert all(_pixel(canvas, x, 8) == BLACK for x in range(12))


# -- output -----------------------------------------------------------------


def test_ppm_is_a_p6_header_followed_by_raw_rgb():
    canvas = Canvas(3, 2, RED)

    data = canvas.to_ppm()

    assert data.startswith(b"P6\n3 2\n255\n")
    assert len(data) == len(b"P6\n3 2\n255\n") + 3 * 2 * 3


def test_writing_a_frame_creates_missing_parent_directories(tmp_path):
    target = tmp_path / "frames" / "nested" / "scene_0000.ppm"

    written = Canvas(2, 2, RED).write_ppm(target)

    assert target.exists()
    assert target.read_bytes().startswith(b"P6\n2 2\n255\n")
    # Paths are normalised to forward slashes so artifact rows read the same on
    # Windows as they do elsewhere.
    assert "\\" not in written
