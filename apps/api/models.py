from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Column
from sqlalchemy.dialects.sqlite import JSON as SQLITE_JSON
from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.utcnow()


class Project(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    name: str
    niche: str
    brand_profile_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(SQLITE_JSON, nullable=False))
    defaults_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(SQLITE_JSON, nullable=False))
    created_at: datetime = Field(default_factory=_now, nullable=False)


class Run(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    project_id: str = Field(index=True)
    status: str = Field(index=True, default="queued")
    stage: str = Field(default="queued")
    progress: float = Field(default=0.0)
    created_at: datetime = Field(default_factory=_now, nullable=False)
    started_at: datetime | None = Field(default=None)
    completed_at: datetime | None = Field(default=None)
    error: str | None = Field(default=None)
    config_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(SQLITE_JSON, nullable=False))


class Artifact(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    run_id: str = Field(index=True)
    type: str = Field(index=True)
    path: str
    created_at: datetime = Field(default_factory=_now, nullable=False)
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(SQLITE_JSON, nullable=False))

