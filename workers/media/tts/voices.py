"""
Voice assignment for multi-speaker episodes.

Phase 2's premise is a *two-speaker* podcast. Before this module every turn was
synthesised with the same default system voice, so the host and the guest were
audibly the same person. `assign_voices` gives each distinct speaker its own
system voice, deterministically, so a re-run of the same script produces the
same casting.

Deliberately narrow: this is SAPI/pyttsx3 voice selection only, not the full
Fish Speech `VoiceRegistry` the Tier-1 plan calls for in a later pass.
"""

from __future__ import annotations

from dataclasses import dataclass

# Preference order per role. First substring that matches an installed voice
# wins; roles then fall back to round-robin over whatever is left.
_ROLE_PREFERENCES: dict[str, tuple[str, ...]] = {
    "host": ("david", "mark", "george", "male"),
    "guest": ("zira", "hazel", "susan", "female"),
}

# pyttsx3/SAPI `rate` is words-per-minute-ish, so the schema's `pace_wpm` maps
# across directly. Clamped to the range SAPI stays intelligible in.
MIN_RATE = 90
MAX_RATE = 220
DEFAULT_RATE = 175


@dataclass(frozen=True)
class SystemVoice:
    id: str
    name: str


@dataclass(frozen=True)
class VoiceAssignment:
    speaker: str
    voice_id: str | None
    voice_name: str


def pace_to_rate(pace_wpm: int | None) -> int:
    """Map a DialogueTurn.pace_wpm onto a SAPI rate."""
    if not pace_wpm:
        return DEFAULT_RATE
    return max(MIN_RATE, min(MAX_RATE, int(pace_wpm)))


def _match(voices: list[SystemVoice], hints: tuple[str, ...], taken: set[str]) -> SystemVoice | None:
    for hint in hints:
        for v in voices:
            if v.id in taken:
                continue
            haystack = f"{v.name} {v.id}".lower()
            if hint in haystack:
                return v
    return None


def assign_voices(
    speakers: list[str],
    voices: list[SystemVoice],
    roles: dict[str, str] | None = None,
) -> dict[str, VoiceAssignment]:
    """
    Map each distinct speaker name onto a system voice.

    `speakers` is in script order; `roles` optionally maps speaker name -> "host"
    or "guest" so preferred voices can be honoured. Speakers beyond the number of
    installed voices wrap around — with only one voice installed everyone shares
    it, which is a system limitation rather than a bug.
    """
    ordered: list[str] = []
    for s in speakers:
        if s and s not in ordered:
            ordered.append(s)

    if not voices:
        return {s: VoiceAssignment(speaker=s, voice_id=None, voice_name="system-default") for s in ordered}

    roles = roles or {}
    assignments: dict[str, VoiceAssignment] = {}
    taken: set[str] = set()

    # Pass 1: honour role preferences.
    for speaker in ordered:
        hints = _ROLE_PREFERENCES.get((roles.get(speaker) or "").lower())
        if not hints:
            continue
        match = _match(voices, hints, taken)
        if match:
            taken.add(match.id)
            assignments[speaker] = VoiceAssignment(speaker, match.id, match.name)

    # Pass 2: everyone else takes the next unused voice, then wraps around.
    cursor = 0
    for speaker in ordered:
        if speaker in assignments:
            continue
        unused = [v for v in voices if v.id not in taken]
        if unused:
            chosen = unused[0]
            taken.add(chosen.id)
        else:
            chosen = voices[cursor % len(voices)]
            cursor += 1
        assignments[speaker] = VoiceAssignment(speaker, chosen.id, chosen.name)

    return assignments
