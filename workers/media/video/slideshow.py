"""
The shipped compositor.

What this is
------------
A turn-aware episode cut: a two-up speaker stage where the talking speaker is
lit and the other is dimmed, a lower-third naming them, burned captions, and a
B-roll slot panel populated from the script's `broll_cues`. One still frame is
drawn per caption and the whole thing is muxed against `episode.wav`.

What this is not
----------------
Lip sync. The Tier-1 plan names MuseTalk (LivePortrait as fallback) for that, and
both need a GPU and downloaded model weights. Rather than make the video stage
fail on every machine that lacks them, this renderer produces a real, watchable
episode from the same inputs a neural backend would consume — audio plus the turn
timeline — and registers against the same protocol. Adding MuseTalk later is a
registration in `registry.py`, not a change to the orchestrator.

Everything here draws through `raster.py`, so the stage has no imaging
dependency; the only external requirement is FFmpeg for the final mux, and its
absence downgrades the output to a storyboard rather than failing the run.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from ..ffmpeg import ffmpeg_available
from .base import EpisodeVideoSpec, RenderResult, SpeakerStyle
from .mux import mux_frames_to_mp4, write_concat_file
from .raster import Canvas, text_width

log = logging.getLogger(__name__)

# Deliberately muted; captions and the active speaker should be the only things
# competing for attention.
BACKDROP = (13, 16, 26)
PANEL = (23, 28, 43)
PANEL_ACTIVE = (33, 41, 64)
TEXT = (233, 237, 246)
TEXT_DIM = (140, 150, 172)
CAPTION_BG = (0, 0, 0)

#: Accents are assigned by a hash of the speaker's name, so the host keeps the
#: same colour across every run and every re-render of an episode.
ACCENTS: tuple[tuple[int, int, int], ...] = (
    (99, 102, 241),   # indigo
    (236, 72, 153),   # pink
    (34, 197, 94),    # green
    (249, 115, 22),   # orange
    (14, 165, 233),   # sky
    (168, 85, 247),   # purple
)


def accent_for(name: str) -> tuple[int, int, int]:
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()
    return ACCENTS[int(digest[:8], 16) % len(ACCENTS)]


def initials(name: str) -> str:
    parts = [p for p in name.replace("_", " ").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[1][0]).upper()


def build_speaker_styles(turns: list[dict], roles: dict[str, str] | None = None) -> dict[str, SpeakerStyle]:
    """Assign a stable accent and role to every speaker in the episode."""
    roles = roles or {}
    styles: dict[str, SpeakerStyle] = {}
    for turn in turns:
        name = str(turn.get("speaker") or "Speaker")
        if name not in styles:
            styles[name] = SpeakerStyle(name=name, role=roles.get(name, "host"), accent=accent_for(name))
    return styles


def _cues_at(turns: list[dict], at_ms: int) -> list[str]:
    """B-roll cues of the turn covering `at_ms`."""
    for turn in turns:
        if int(turn.get("start_ms", 0)) <= at_ms < int(turn.get("end_ms", 0)):
            return [str(c) for c in (turn.get("broll_cues") or [])]
    return []


def build_scenes(spec: EpisodeVideoSpec) -> list[dict]:
    """
    Flatten captions (or bare turns) into gapless, ordered scenes.

    Each scene runs until the next one begins, so the video covers the audio with
    no uncovered stretch — a gap in the playlist would freeze on whatever frame
    preceded it and desynchronise everything after.
    """
    units: list[dict] = []
    if spec.show_captions and spec.captions:
        for cap in spec.captions:
            units.append(
                {"start_ms": cap.start_ms, "end_ms": cap.end_ms, "speaker": cap.speaker, "lines": list(cap.lines)}
            )
    else:
        for turn in spec.turns:
            if not (turn.get("text") or "").strip():
                continue
            units.append(
                {
                    "start_ms": int(turn.get("start_ms", 0)),
                    "end_ms": int(turn.get("end_ms", 0)),
                    "speaker": str(turn.get("speaker") or "Speaker"),
                    "lines": [],
                }
            )

    if not units:
        return []

    units.sort(key=lambda u: u["start_ms"])
    total = max(spec.duration_ms, units[-1]["end_ms"])

    scenes: list[dict] = []
    for i, unit in enumerate(units):
        start = 0 if i == 0 else unit["start_ms"]
        end = units[i + 1]["start_ms"] if i + 1 < len(units) else max(total, unit["end_ms"])
        if end <= start:
            end = start + 1
        scenes.append(
            {
                "index": i,
                "start_ms": start,
                "end_ms": end,
                "speaker": unit["speaker"],
                "lines": unit["lines"],
                "broll_cues": _cues_at(spec.turns, (unit["start_ms"] + unit["end_ms"]) // 2),
            }
        )
    return scenes


class SlideshowRenderer:
    """A `VideoRenderer` that composes an episode from drawn still frames."""

    name = "slideshow"

    def available(self) -> tuple[bool, str]:
        if not ffmpeg_available():
            return False, "ffmpeg not found on PATH; storyboard and captions only, no mp4"
        return True, "ok"

    # -- drawing -----------------------------------------------------------

    def _draw_scene(self, spec: EpisodeVideoSpec, scene: dict, width: int, height: int) -> Canvas:
        canvas = Canvas(width, height, BACKDROP)
        active = scene["speaker"]
        style = spec.speakers.get(active) or SpeakerStyle(name=active)
        cues = scene["broll_cues"] if spec.show_broll_slots else []
        lines = list(scene["lines"]) if (spec.show_captions and scene["lines"]) else []

        # Every size derives from `unit`, so the layout holds at any frame size —
        # including the 9:16 exports Phase 4 will ask for.
        unit = max(2, height // 90)
        pad = unit

        # Vertical bands are measured from real glyph height (`7 * scale`) and
        # laid out bottom-up. Advancing by round multiples of `unit` instead is
        # what made the first cut overlap its own lower-third.
        title_scale = max(1, (unit * 3) // 4)
        title_h = 7 * title_scale + pad * 2

        name_scale = max(2, (unit * 3) // 4)
        role_scale = max(1, unit // 3)
        lt_h = 7 * name_scale + 7 * role_scale + pad * 3

        cap_scale = max(2, unit)
        cap_line_h = 7 * cap_scale + cap_scale
        cap_h = (cap_line_h * len(lines) + pad * 2) if lines else 0

        prog_h = max(2, unit // 2)

        cap_y = height - prog_h - cap_h
        lt_y = (cap_y if lines else height - prog_h) - lt_h - pad
        stage_top = title_h + pad
        stage_bottom = max(stage_top + unit * 4, lt_y - pad)

        # --- title bar ---
        canvas.fill_rect(0, 0, width, title_h, PANEL)
        canvas.fill_rect(0, title_h - max(1, unit // 3), width, max(1, unit // 3), style.accent)
        canvas.text(pad * 2, pad, _clip(spec.title, (width - pad * 4) // (6 * title_scale)), TEXT, title_scale)

        # --- stage ---
        stage_right = int(width * 0.62) if cues else width - pad * 2
        self._draw_speakers(canvas, spec, active, pad * 2, stage_top, stage_right - pad * 2, stage_bottom - stage_top)

        if cues:
            self._draw_broll_slot(
                canvas, cues, stage_right + pad, stage_top,
                width - stage_right - pad * 3, stage_bottom - stage_top, unit,
            )

        # --- lower third ---
        canvas.fill_rect(pad * 2, lt_y, max(1, int(width * 0.42)), lt_h, PANEL, alpha=0.95)
        canvas.fill_rect(pad * 2, lt_y, max(2, unit // 2), lt_h, style.accent)
        canvas.text(pad * 4, lt_y + pad, _clip(style.name, 24), TEXT, name_scale)
        canvas.text(pad * 4, lt_y + pad * 2 + 7 * name_scale, style.role.upper(), TEXT_DIM, role_scale)

        # --- captions ---
        if lines:
            self._draw_captions(canvas, lines, width, cap_y, cap_h, cap_scale, cap_line_h, pad)

        # --- progress ticker ---
        if spec.duration_ms > 0:
            done = min(1.0, scene["end_ms"] / spec.duration_ms)
            canvas.fill_rect(0, height - prog_h, width, prog_h, PANEL)
            canvas.fill_rect(0, height - prog_h, int(width * done), prog_h, style.accent)

        return canvas

    def _draw_speakers(self, canvas: Canvas, spec, active: str, x: int, y: int, w: int, h: int) -> None:
        names = list(spec.speakers) or [active]
        gap = max(4, w // 40)
        panel_w = (w - gap * (len(names) - 1)) // len(names) if names else w

        for i, name in enumerate(names):
            style = spec.speakers.get(name) or SpeakerStyle(name=name)
            is_active = name == active
            px = x + i * (panel_w + gap)

            canvas.fill_rect(px, y, panel_w, h, PANEL_ACTIVE if is_active else PANEL)
            if is_active:
                # Accent frame marks who is speaking — this is the turn-aware cut.
                border = max(2, h // 90)
                canvas.fill_rect(px, y, panel_w, border, style.accent)
                canvas.fill_rect(px, y + h - border, panel_w, border, style.accent)
                canvas.fill_rect(px, y, border, h, style.accent)
                canvas.fill_rect(px + panel_w - border, y, border, h, style.accent)

            # Avatar plate with initials, dimmed when the speaker is listening.
            plate = min(panel_w, h) // 2
            cx = px + panel_w // 2
            cy = y + h // 2 - plate // 6
            canvas.fill_rect(cx - plate // 2, cy - plate // 2, plate, plate, style.accent, alpha=1.0 if is_active else 0.35)

            mono = initials(style.name)
            scale = max(2, plate // 12)
            canvas.text_centered(cx, cy - (7 * scale) // 2, mono, TEXT if is_active else TEXT_DIM, scale)

            label_scale = max(1, plate // 26)
            label_y = y + h - 7 * label_scale - max(4, h // 24)
            canvas.text_centered(cx, label_y, _clip(style.name, 18), TEXT if is_active else TEXT_DIM, label_scale)

    def _draw_broll_slot(self, canvas: Canvas, cues: list[str], x: int, y: int, w: int, h: int, unit: int) -> None:
        """
        Render the B-roll overlay slot.

        The slot is wired end to end — cues come from the script and land in the
        frame — but it shows the cue as a titled placeholder rather than
        generated imagery. Actually filling it needs the Stable Diffusion asset
        pipeline, which the plan schedules after the clip engine.
        """
        if w <= unit * 4:
            return
        pad = unit
        scale = max(1, unit // 2)
        line_h = 7 * scale + scale

        canvas.fill_rect(x, y, w, h, PANEL)
        header_h = 7 * scale + pad
        canvas.fill_rect(x, y, w, header_h, (44, 52, 78))
        canvas.text(x + pad, y + pad // 2, "B-ROLL", TEXT_DIM, scale)

        max_chars = max(6, (w - pad * 2) // (6 * scale))
        cursor = y + header_h + pad
        for cue in cues[:3]:
            for line in _wrap(cue, max_chars)[:2]:
                if cursor + 7 * scale > y + h - pad:
                    return
                canvas.text(x + pad, cursor, line, TEXT, scale)
                cursor += line_h
            cursor += pad

    def _draw_captions(
        self, canvas: Canvas, lines: list[str], width: int, band_y: int, band_h: int,
        scale: int, line_h: int, pad: int,
    ) -> None:
        canvas.fill_rect(0, band_y, width, band_h, CAPTION_BG, alpha=0.62)
        for i, line in enumerate(lines):
            # Shrink rather than overflow if a line is still too wide for the
            # frame — wrap_text counts characters, not pixels.
            s = scale
            while s > 1 and text_width(line, s) > width - pad * 4:
                s -= 1
            canvas.text_centered(width // 2, band_y + pad + i * line_h, line, TEXT, s)

    # -- rendering ---------------------------------------------------------

    def render(self, spec: EpisodeVideoSpec) -> RenderResult:
        result = RenderResult()

        scenes = build_scenes(spec)
        if not scenes:
            raise RuntimeError("no scenes to render: the episode timeline is empty")

        # yuv420p needs even dimensions; an odd frame size fails at encode time
        # with an error that says nothing about the cause.
        width = max(160, spec.width - (spec.width % 2))
        height = max(120, spec.height - (spec.height % 2))

        frames_dir = Path(spec.out_dir) / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)

        playlist: list[tuple[str, float]] = []
        for scene in scenes:
            canvas = self._draw_scene(spec, scene, width, height)
            frame_path = frames_dir / f"scene_{scene['index']:04d}.ppm"
            canvas.write_ppm(frame_path)
            seconds = max(0.04, (scene["end_ms"] - scene["start_ms"]) / 1000.0)
            playlist.append((str(frame_path), seconds))
            result.frame_paths.append(str(frame_path).replace("\\", "/"))
            scene["frame"] = str(frame_path).replace("\\", "/")

        result.scenes = scenes

        concat_path = write_concat_file(frames_dir, playlist)
        ok, detail = self.available()
        if not ok:
            result.notes.append(detail)
            return result

        out_path = Path(spec.out_dir) / "episode.mp4"
        muxed, detail = mux_frames_to_mp4(concat_path, spec.audio_path, str(out_path), fps=spec.fps)
        if muxed:
            result.video_path = str(out_path).replace("\\", "/")
        else:
            result.notes.append(f"video mux failed: {detail}")
        return result


def _clip(text: str, max_chars: int) -> str:
    text = " ".join(str(text).split())
    if max_chars <= 1 or len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "-"


def _wrap(text: str, max_chars: int) -> list[str]:
    from .captions import wrap_text

    return wrap_text(text, max_chars)
