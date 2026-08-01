from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

# Portable JSON. The column was previously `sqlalchemy.dialects.sqlite.JSON`,
# which pins the schema to SQLite and fails outright on Postgres — the blocker
# that surfaces the moment the app leaves one machine.
#
# The variant keeps the generic type everywhere and upgrades to JSONB only on
# Postgres, where it is the strictly better choice: binary storage, and it can
# be indexed if a query ever needs to reach inside `config_json`.
_JSON = JSON().with_variant(JSONB, "postgresql")


def _json_column() -> Column:
    # A fresh Column per field: SQLAlchemy binds a Column to one table, so a
    # shared instance would silently attach to whichever model imported last.
    return Column(_JSON, nullable=False)


def _now() -> datetime:
    # datetime.utcnow() is deprecated in Python 3.12+. Stored naive (UTC) to stay
    # compatible with the existing SQLite columns.
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Project(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    name: str
    niche: str
    brand_profile_json: dict[str, Any] = Field(default_factory=dict, sa_column=_json_column())
    defaults_json: dict[str, Any] = Field(default_factory=dict, sa_column=_json_column())
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
    config_json: dict[str, Any] = Field(default_factory=dict, sa_column=_json_column())


class Artifact(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    run_id: str = Field(index=True)
    type: str = Field(index=True)
    path: str
    created_at: datetime = Field(default_factory=_now, nullable=False)
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=_json_column())

