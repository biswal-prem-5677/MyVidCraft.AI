from __future__ import annotations

import os

from sqlmodel import Session, SQLModel, create_engine

from .settings import settings


def _ensure_parent_dir(db_url: str) -> None:
    if db_url.startswith("sqlite:///./"):
        path = db_url.removeprefix("sqlite:///./")
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)


_ensure_parent_dir(settings.database_url)
engine = create_engine(settings.database_url, echo=False)


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session

