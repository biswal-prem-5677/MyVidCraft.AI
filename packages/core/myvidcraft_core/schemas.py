from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Platform(str, Enum):
    tiktok = "tiktok"
    instagram = "instagram"
    youtube_shorts = "youtube_shorts"
    youtube = "youtube"
    podcast = "podcast"


class DialogueMode(str, Enum):
    debate = "debate"
    storytelling = "storytelling"
    teaching = "teaching"


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    niche: str = Field(min_length=1, max_length=160)
    brand_profile: dict[str, Any] = Field(default_factory=dict)
    defaults: dict[str, Any] = Field(default_factory=dict)


class ProjectOut(BaseModel):
    id: str
    name: str
    niche: str
    brand_profile: dict[str, Any]
    defaults: dict[str, Any]
    created_at: datetime


class ContentCalendarItem(BaseModel):
    date: str
    platform: Platform
    topic: str
    hook: str
    format: str
    cta: str
    asset_prompts: list[str] = Field(default_factory=list)
    hook_score: float = Field(ge=0, le=1, default=0.5)


class StrategyPack(BaseModel):
    niche: str
    brand_summary: str
    viral_topic_slate: list[str]
    hook_library: list[str]
    calendar: list[ContentCalendarItem]
    notes: list[str] = Field(default_factory=list)


class CharacterProfile(BaseModel):
    name: str
    role: Literal["host", "guest"]
    tone: str = "calm"
    rhetoric: str = "teacher"
    beliefs: list[str] = Field(default_factory=list)
    taboos: list[str] = Field(default_factory=list)
    catchphrases: list[str] = Field(default_factory=list)
    interrupt_style: Literal["none", "rare", "often"] = "rare"
    humor_level: int = Field(ge=0, le=10, default=4)


class EpisodeBeat(BaseModel):
    title: str
    goal: str
    intended_emotion: str = "curious"


class EpisodePlan(BaseModel):
    title: str
    premise: str
    stance: str
    mode: DialogueMode
    beats: list[EpisodeBeat]
    characters: list[CharacterProfile]
    constraints: dict[str, Any] = Field(default_factory=dict)


class DialogueTurn(BaseModel):
    speaker: str
    text: str
    emotion: str = "neutral"
    pace_wpm: int = Field(ge=90, le=220, default=150)
    pause_before_ms: int = Field(ge=0, le=5000, default=0)
    pause_after_ms: int = Field(ge=0, le=8000, default=400)
    broll_cues: list[str] = Field(default_factory=list)
    safety_flags: list[str] = Field(default_factory=list)


class RunCreate(BaseModel):
    mode: DialogueMode = DialogueMode.teaching
    platforms: list[Platform] = Field(default_factory=lambda: [Platform.youtube, Platform.podcast])
    language: str = "en"
    config: dict[str, Any] = Field(default_factory=dict)


class RunStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class ArtifactType(str, Enum):
    strategy = "strategy"
    episode_plan = "episode_plan"
    dialogue = "dialogue"
    run_config = "run_config"
    turn_audio_wav = "turn_audio_wav"
    episode_audio_wav = "episode_audio_wav"
    episode_audio_mp3 = "episode_audio_mp3"


class ArtifactOut(BaseModel):
    id: str
    run_id: str
    type: ArtifactType
    path: str
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunOut(BaseModel):
    id: str
    project_id: str
    status: RunStatus
    stage: str
    progress: float = Field(ge=0, le=1)
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)