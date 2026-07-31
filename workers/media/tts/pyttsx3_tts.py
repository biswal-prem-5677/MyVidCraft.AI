"""
Offline TTS via the local system speech engine (Windows SAPI through pyttsx3).

The default `SpeechEngine` implementation: free, offline, and present on every
Windows box, which is why Phase 2 shipped against it. Resolve it through
`workers.media.tts.registry.get_engine()` rather than importing it directly.

Two reliability details this module exists to handle:

1. **COM initialisation.** SAPI is a COM API and must be initialised per thread.
   The API drives runs from a background thread, so `CoInitialize` is called
   there explicitly instead of relying on it having happened by accident.
2. **Serialisation.** The system speech engine is a shared, single-instance
   resource. Two concurrent runs synthesising at once corrupt each other's
   output, so synthesis is guarded by a process-wide lock.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from pathlib import Path

from .base import Voice
from .voices import DEFAULT_RATE

log = logging.getLogger(__name__)

# SAPI is a single shared engine; never synthesise from two threads at once.
_synth_lock = threading.Lock()

_voices_cache: list[Voice] | None = None
_voices_lock = threading.Lock()


@contextmanager
def _com_context():
    """Initialise COM for this thread if we're on Windows and it isn't already."""
    initialised = False
    try:
        import pythoncom  # type: ignore
    except ImportError:
        pythoncom = None  # non-Windows, or pywin32 absent

    if pythoncom is not None:
        try:
            pythoncom.CoInitialize()
            initialised = True
        except Exception as e:  # already initialised on this thread is fine
            log.debug("CoInitialize skipped: %s", e)

    try:
        yield
    finally:
        if initialised and pythoncom is not None:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass


def list_system_voices(refresh: bool = False) -> list[Voice]:
    """Enumerate installed system voices. Cached; returns [] if unavailable."""
    global _voices_cache
    if _voices_cache is not None and not refresh:
        return _voices_cache

    with _voices_lock:
        if _voices_cache is not None and not refresh:
            return _voices_cache
        found: list[Voice] = []
        try:
            import pyttsx3

            with _com_context():
                engine = pyttsx3.init()
                try:
                    for v in engine.getProperty("voices"):
                        vid = getattr(v, "id", "") or ""
                        name = getattr(v, "name", "") or vid
                        if vid:
                            found.append(Voice(id=vid, name=name))
                finally:
                    try:
                        engine.stop()
                    except Exception:
                        pass
        except Exception as e:
            log.warning("could not enumerate system voices (%s)", e)
            found = []
        _voices_cache = found
    return _voices_cache


def text_to_wav(
    text: str,
    out_path: str,
    rate: int = DEFAULT_RATE,
    voice_id: str | None = None,
) -> None:
    """
    Synthesise `text` to a WAV file.

    `voice_id` is a system voice id from `list_system_voices()`; `None` uses the
    system default. `rate` is words-per-minute (see `voices.pace_to_rate`).
    """
    import pyttsx3

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    with _synth_lock, _com_context():
        engine = pyttsx3.init()
        try:
            engine.setProperty("rate", int(rate))
            if voice_id:
                try:
                    engine.setProperty("voice", voice_id)
                except Exception as e:
                    # A missing voice must not fail the turn — fall back to default.
                    log.warning("could not select voice %s (%s); using default", voice_id, e)
            engine.save_to_file(text, out_path)
            engine.runAndWait()
        finally:
            try:
                engine.stop()
            except Exception:
                pass


class Pyttsx3Engine:
    """`SpeechEngine` over the local system speech engine.

    A thin adapter over the module functions above, which hold the COM and
    locking behaviour. Stateless — the caches and locks are module scoped
    because the underlying engine is a process-wide resource, not a per-instance
    one.
    """

    name = "pyttsx3"

    def list_voices(self) -> list[Voice]:
        return list_system_voices()

    def synthesize(self, text: str, out_path: str, *, rate: int = DEFAULT_RATE, voice_id: str | None = None) -> None:
        text_to_wav(text=text, out_path=out_path, rate=rate, voice_id=voice_id)
