"""
LLM-facing mirrors of the strict domain schemas in `schemas.py`.

Why these exist
---------------
The Anthropic structured-outputs JSON Schema subset does **not** support numeric
constraints (`minimum`/`maximum`) or string-length constraints. The Python SDK
strips them from the schema it sends and validates them client-side instead —
which means a model that returns `pace_wpm: 260` would raise a `ValidationError`
*after* a paid round trip rather than being corrected.

So the model is asked for bound-free shapes, and `to_domain()` clamps into the
strict types that the rest of the pipeline (TTS, stitching, UI) depends on.
Fields the model has no business inventing — calendar dates, dialogue mode,
run constraints — are supplied deterministically by the caller, not generated.

Every field is required: the structured-outputs subset needs `additionalProperties:
false` plus an explicit `required` list, and optional fields there invite
half-populated objects.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from .schemas import (
    CharacterProfile,
    ContentCalendarItem,
    DialogueMode,
    DialogueTurn,
    EpisodeBeat,
    EpisodePlan,
    Platform,
)


def _clamp_int(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(value)))


def _clamp_float(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _trim(value: str, limit: int) -> str:
    value = (value or "").strip()
    return value[:limit]


# --------------------------------------------------------------------------
# Dialogue
# --------------------------------------------------------------------------


class LLMDialogueTurn(BaseModel):
    speaker: str
    text: str
    emotion: str
    pace_wpm: int
    pause_before_ms: int
    pause_after_ms: int
    broll_cues: list[str]
    safety_flags: list[str]

    def to_domain(self) -> DialogueTurn:
        return DialogueTurn(
            speaker=_trim(self.speaker, 60) or "Host",
            text=_trim(self.text, 2000),
            emotion=_trim(self.emotion, 40) or "neutral",
            pace_wpm=_clamp_int(self.pace_wpm, 90, 220),
            pause_before_ms=_clamp_int(self.pause_before_ms, 0, 5000),
            pause_after_ms=_clamp_int(self.pause_after_ms, 0, 8000),
            broll_cues=[_trim(c, 300) for c in self.broll_cues if c and c.strip()],
            safety_flags=[_trim(f, 80) for f in self.safety_flags if f and f.strip()],
        )


class LLMDialogueScript(BaseModel):
    """Wrapper: structured outputs requires a top-level object, not a bare array."""

    turns: list[LLMDialogueTurn]

    def to_domain(self) -> list[DialogueTurn]:
        return [t.to_domain() for t in self.turns if t.text and t.text.strip()]


# --------------------------------------------------------------------------
# Episode plan
# --------------------------------------------------------------------------


class LLMEpisodeBeat(BaseModel):
    title: str
    goal: str
    intended_emotion: str

    def to_domain(self) -> EpisodeBeat:
        return EpisodeBeat(
            title=_trim(self.title, 160) or "Beat",
            goal=_trim(self.goal, 400),
            intended_emotion=_trim(self.intended_emotion, 40) or "curious",
        )


class LLMCharacterProfile(BaseModel):
    name: str
    role: Literal["host", "guest"]
    tone: str
    rhetoric: str
    beliefs: list[str]
    taboos: list[str]
    catchphrases: list[str]
    interrupt_style: Literal["none", "rare", "often"]
    humor_level: int

    def to_domain(self) -> CharacterProfile:
        return CharacterProfile(
            name=_trim(self.name, 60) or "Host",
            role=self.role,
            tone=_trim(self.tone, 40) or "calm",
            rhetoric=_trim(self.rhetoric, 40) or "teacher",
            beliefs=[_trim(b, 200) for b in self.beliefs if b and b.strip()],
            taboos=[_trim(t, 200) for t in self.taboos if t and t.strip()],
            catchphrases=[_trim(c, 200) for c in self.catchphrases if c and c.strip()],
            interrupt_style=self.interrupt_style,
            humor_level=_clamp_int(self.humor_level, 0, 10),
        )


class LLMEpisodePlan(BaseModel):
    title: str
    premise: str
    stance: str
    beats: list[LLMEpisodeBeat]
    characters: list[LLMCharacterProfile]

    def to_domain(self, mode: DialogueMode, constraints: dict) -> EpisodePlan:
        return EpisodePlan(
            title=_trim(self.title, 200) or "Untitled episode",
            premise=_trim(self.premise, 1000),
            stance=_trim(self.stance, 1000),
            mode=mode,
            beats=[b.to_domain() for b in self.beats],
            characters=[c.to_domain() for c in self.characters],
            constraints=constraints,
        )


# --------------------------------------------------------------------------
# Strategy pack
# --------------------------------------------------------------------------


class LLMCalendarItem(BaseModel):
    """No `date` field on purpose — the caller assigns real dates."""

    platform: Platform
    topic: str
    hook: str
    format: str
    cta: str
    asset_prompts: list[str]
    hook_score: float

    def to_domain(self, date_str: str) -> ContentCalendarItem:
        return ContentCalendarItem(
            date=date_str,
            platform=self.platform,
            topic=_trim(self.topic, 300) or "Untitled topic",
            hook=_trim(self.hook, 300),
            format=_trim(self.format, 60) or "talking_head",
            cta=_trim(self.cta, 200),
            asset_prompts=[_trim(p, 400) for p in self.asset_prompts if p and p.strip()],
            hook_score=_clamp_float(self.hook_score, 0.0, 1.0),
        )


class LLMStrategyPack(BaseModel):
    brand_summary: str
    viral_topic_slate: list[str]
    hook_library: list[str]
    calendar: list[LLMCalendarItem]
    notes: list[str]
