from __future__ import annotations

import logging
import os
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

from .settings import settings

log = logging.getLogger(__name__)

# Databases written before the project was renamed. Adopted on first start so the
# rebrand does not orphan existing runs and their on-disk artifacts.
_LEGACY_DB_NAMES = ("zentcast.sqlite3", "pulsecast.sqlite3")


def _sqlite_path(db_url: str) -> Path | None:
    if not db_url.startswith("sqlite:///"):
        return None
    raw = db_url.removeprefix("sqlite:///")
    if raw.startswith("./"):
        raw = raw[2:]
    return Path(raw)


def _prepare_sqlite(db_url: str) -> None:
    path = _sqlite_path(db_url)
    if path is None:
        return

    if path.parent and str(path.parent) not in ("", "."):
        os.makedirs(path.parent, exist_ok=True)

    # Only adopt a legacy file when there is no current one — never overwrite.
    if path.exists():
        return
    for legacy_name in _LEGACY_DB_NAMES:
        legacy = path.parent / legacy_name
        if legacy.exists():
            legacy.rename(path)
            log.info("adopted legacy database %s as %s", legacy, path)
            return


_prepare_sqlite(settings.database_url)
engine = create_engine(settings.database_url, echo=False)


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
