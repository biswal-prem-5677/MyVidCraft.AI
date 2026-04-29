from __future__ import annotations

from pathlib import Path

import pyttsx3


def text_to_wav(text: str, out_path: str, rate: int = 175, voice_hint: str | None = None) -> None:
    """
    Offline, local TTS using Windows SAPI via pyttsx3.
    Good enough for Phase 2 vertical slice (no external APIs).
    """
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    engine = pyttsx3.init()
    try:
        engine.setProperty("rate", rate)
        if voice_hint:
            try:
                for v in engine.getProperty("voices"):
                    name = (getattr(v, "name", "") or "").lower()
                    vid = (getattr(v, "id", "") or "").lower()
                    if voice_hint.lower() in name or voice_hint.lower() in vid:
                        engine.setProperty("voice", v.id)
                        break
            except Exception:
                pass
        engine.save_to_file(text, out_path)
        engine.runAndWait()
    finally:
        try:
            engine.stop()
        except Exception:
            pass

