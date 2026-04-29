from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .settings import settings


def run_dir(run_id: str) -> Path:
    base = Path(settings.artifacts_dir)
    p = base / run_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_json(run_id: str, filename: str, payload: Any) -> str:
    p = run_dir(run_id) / filename
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return str(p).replace("\\", "/")


def ensure_data_dirs() -> None:
    os.makedirs(settings.artifacts_dir, exist_ok=True)

