"""
Shared test setup.

The API's `Settings` object and the SQLModel engine are both constructed at
import time, so the isolated database and artifacts directory have to be in the
environment *before* anything under `apps/` is imported. That is why this block
runs at module scope rather than in a fixture.
"""

from __future__ import annotations

import math
import os
import shutil
import struct
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="myvidcraft-tests-"))

# f"sqlite:///{posix}" yields three slashes for a Windows path (C:/...) and four
# for a POSIX one (/tmp/...), which is what SQLAlchemy wants in each case.
os.environ["MYVIDCRAFT_DATABASE_URL"] = f"sqlite:///{(_TMP / 'test.sqlite3').as_posix()}"
os.environ["MYVIDCRAFT_ARTIFACTS_DIR"] = str(_TMP / "artifacts")
# Never reach for a real model or a real broker from tests.
os.environ["MYVIDCRAFT_LLM_ENABLED"] = "false"
os.environ["MYVIDCRAFT_ANTHROPIC_API_KEY"] = ""

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _cleanup_tmp():
    yield
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture(scope="session")
def artifacts_dir() -> Path:
    from apps.api.settings import settings

    path = Path(settings.artifacts_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def tmp_wav_dir(tmp_path: Path) -> Path:
    return tmp_path


def _pcm16_sine(seconds: float, framerate: int, amplitude: int, freq: float = 220.0) -> bytes:
    count = int(seconds * framerate)
    return b"".join(
        struct.pack("<h", int(amplitude * math.sin(2 * math.pi * freq * i / framerate))) for i in range(count)
    )


@pytest.fixture
def make_wav():
    """Write a small mono 16-bit WAV and return its path."""

    def _make(
        path: Path,
        seconds: float = 0.05,
        framerate: int = 22050,
        amplitude: int = 8000,
        nchannels: int = 1,
        sampwidth: int = 2,
    ) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        frames = _pcm16_sine(seconds, framerate, amplitude)
        if sampwidth != 2:
            frames = b"\x00" * (int(seconds * framerate) * sampwidth * nchannels)
        if nchannels == 2 and sampwidth == 2:
            frames = b"".join(frames[i : i + 2] * 2 for i in range(0, len(frames), 2))
        with wave.open(str(path), "wb") as w:
            w.setnchannels(nchannels)
            w.setsampwidth(sampwidth)
            w.setframerate(framerate)
            w.writeframes(frames)
        return path

    return _make


@pytest.fixture
def pcm16_sine():
    """Raw 16-bit PCM frames, for testing the normalizer directly."""
    return _pcm16_sine


@pytest.fixture
def client(monkeypatch):
    """
    FastAPI test client with the pipeline stubbed out.

    Both kickoff paths report "celery" so the API never starts a real render —
    a live run would drive the system speech engine for minutes.
    """
    from fastapi.testclient import TestClient

    from workers.orchestrator import tasks

    monkeypatch.setattr(tasks, "kickoff", lambda run_id: "celery")
    monkeypatch.setattr(tasks, "kickoff_voice", lambda run_id: "celery")

    from apps.api.main import app

    with TestClient(app) as c:
        yield c
