"""
A tiny software rasterizer.

Why this exists
---------------
The compositor needs to draw solid panels and legible text onto frames. The
obvious tool is Pillow, but the project's standing rule is that a run completes
on a machine with nothing installed but Python — the same reason `normalize.py`
implements loudness over `array` instead of pulling in pydub. Adding a compiled
imaging dependency for what amounts to rectangles and a caption font would break
that rule for the least interesting part of the pipeline.

So: an RGB byte buffer, filled rectangles, and a 5x7 bitmap font, written out as
binary PPM (P6). PPM is a 15-byte header followed by raw RGB, which FFmpeg reads
natively — no encoder needed on this side of the seam.

Scope limit worth knowing: the font covers printable ASCII only. Anything else
renders as `?`. Captions in a non-Latin script therefore need a real font stack,
which is a Phase 5 concern alongside the multilingual work — see
`SUBSTITUTE` and `text()`.
"""

from __future__ import annotations

from pathlib import Path

FONT_WIDTH = 5
FONT_HEIGHT = 7
#: One blank column between glyphs, so an advance is 6 columns at scale 1.
FONT_ADVANCE = FONT_WIDTH + 1

#: Drawn in place of any character the font does not cover.
SUBSTITUTE = "?"

RGB = tuple[int, int, int]

# Printable ASCII 32..126, five column-bitmaps per glyph. Bit 0 is the top row,
# bit 6 the bottom; the high bit is unused. This is the classic 5x7 cell used by
# character LCDs, transcribed here so the table has no external source to drift
# from.
_FONT_5X7_HEX = (
    "0000000000"  # (space)
    "00005F0000"  # !
    "0007000700"  # "
    "147F147F14"  # #
    "242A7F2A12"  # $
    "2313086462"  # %
    "3649552250"  # &
    "0005030000"  # '
    "001C224100"  # (
    "0041221C00"  # )
    "14083E0814"  # *
    "08083E0808"  # +
    "0050300000"  # ,
    "0808080808"  # -
    "0060600000"  # .
    "2010080402"  # /
    "3E5149453E"  # 0
    "00427F4000"  # 1
    "4261514946"  # 2
    "2141454B31"  # 3
    "1814127F10"  # 4
    "2745454539"  # 5
    "3C4A494930"  # 6
    "0171090503"  # 7
    "3649494936"  # 8
    "064949291E"  # 9
    "0036360000"  # :
    "0056360000"  # ;
    "0008142241"  # <
    "1414141414"  # =
    "4122140800"  # >
    "0201510906"  # ?
    "324979413E"  # @
    "7E1111117E"  # A
    "7F49494936"  # B
    "3E41414122"  # C
    "7F4141221C"  # D
    "7F49494941"  # E
    "7F09090101"  # F
    "3E4149497A"  # G
    "7F0808087F"  # H
    "00417F4100"  # I
    "2040413F01"  # J
    "7F08142241"  # K
    "7F40404040"  # L
    "7F0204027F"  # M
    "7F0408107F"  # N
    "3E4141413E"  # O
    "7F09090906"  # P
    "3E4151215E"  # Q
    "7F09192946"  # R
    "4649494931"  # S
    "01017F0101"  # T
    "3F4040403F"  # U
    "1F2040201F"  # V
    "7F2018207F"  # W
    "6314081463"  # X
    "0304780403"  # Y
    "6151494543"  # Z
    "007F414100"  # [
    "0204081020"  # backslash
    "0041417F00"  # ]
    "0402010204"  # ^
    "4040404040"  # _
    "0001020400"  # `
    "2054545478"  # a
    "7F48444438"  # b
    "3844444420"  # c
    "384444487F"  # d
    "3854545418"  # e
    "087E090102"  # f
    "0C5252523E"  # g
    "7F08040478"  # h
    "00447D4000"  # i
    "2040443D00"  # j
    "7F10284400"  # k
    "00417F4000"  # l
    "7C04180478"  # m
    "7C08040478"  # n
    "3844444438"  # o
    "7C14141408"  # p
    "081414187C"  # q
    "7C08040408"  # r
    "4854545420"  # s
    "043F444020"  # t
    "3C4040207C"  # u
    "1C2040201C"  # v
    "3C4030403C"  # w
    "4428102844"  # x
    "0C5050503C"  # y
    "4464544C44"  # z
    "0008364100"  # {
    "00007F0000"  # |
    "0041360800"  # }
    "0804081008"  # ~
)

_FONT = bytes.fromhex(_FONT_5X7_HEX)
_FIRST_CHAR = 32
_LAST_CHAR = 126


def _glyph(ch: str) -> bytes:
    code = ord(ch)
    if code < _FIRST_CHAR or code > _LAST_CHAR:
        code = ord(SUBSTITUTE)
    offset = (code - _FIRST_CHAR) * FONT_WIDTH
    return _FONT[offset : offset + FONT_WIDTH]


def text_width(s: str, scale: int = 1) -> int:
    """Pixel width of `s`, including the trailing inter-glyph gap of each char."""
    if not s:
        return 0
    return len(s) * FONT_ADVANCE * scale - scale  # no gap after the last glyph


def text_height(scale: int = 1) -> int:
    return FONT_HEIGHT * scale


class Canvas:
    """A mutable RGB image. Origin is top-left; all drawing clips to bounds."""

    def __init__(self, width: int, height: int, background: RGB = (0, 0, 0)) -> None:
        if width <= 0 or height <= 0:
            raise ValueError(f"canvas must be positive-sized, got {width}x{height}")
        self.width = width
        self.height = height
        self._buf = bytearray(bytes(background) * (width * height))

    def fill_rect(self, x: int, y: int, w: int, h: int, color: RGB, alpha: float = 1.0) -> None:
        """
        Fill a rectangle, clipped to the canvas.

        `alpha` blends against what is already there, which is what makes a
        caption band readable over a bright backdrop without a second buffer.
        """
        if w <= 0 or h <= 0 or alpha <= 0:
            return
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(self.width, x + w), min(self.height, y + h)
        if x0 >= x1 or y0 >= y1:
            return

        opaque = alpha >= 1.0
        row = bytes(color) * (x1 - x0)
        for py in range(y0, y1):
            start = (py * self.width + x0) * 3
            end = start + (x1 - x0) * 3
            if opaque:
                self._buf[start:end] = row
            else:
                existing = self._buf[start:end]
                self._buf[start:end] = bytes(
                    int(src + (dst - src) * alpha) for src, dst in zip(existing, row, strict=True)
                )

    def text(self, x: int, y: int, s: str, color: RGB, scale: int = 1) -> int:
        """Draw `s` with its top-left at (x, y). Returns the advance in pixels."""
        scale = max(1, scale)
        cursor = x
        for ch in s:
            glyph = _glyph(ch)
            for col in range(FONT_WIDTH):
                bits = glyph[col]
                if not bits:
                    continue
                for rowi in range(FONT_HEIGHT):
                    if bits & (1 << rowi):
                        self.fill_rect(cursor + col * scale, y + rowi * scale, scale, scale, color)
            cursor += FONT_ADVANCE * scale
        return cursor - x

    def text_centered(self, cx: int, y: int, s: str, color: RGB, scale: int = 1) -> None:
        self.text(cx - text_width(s, scale) // 2, y, s, color, scale)

    def to_ppm(self) -> bytes:
        """Binary PPM (P6). FFmpeg decodes this without any codec."""
        return b"P6\n%d %d\n255\n" % (self.width, self.height) + bytes(self._buf)

    def write_ppm(self, path: str | Path) -> str:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(self.to_ppm())
        return str(p).replace("\\", "/")
