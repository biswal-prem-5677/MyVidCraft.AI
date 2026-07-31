from workers.media.tts.base import Voice
from workers.media.tts.voices import (
    DEFAULT_RATE,
    MAX_RATE,
    MIN_RATE,
    assign_voices,
    pace_to_rate,
)

DAVID = Voice(id="TTS_MS_EN-US_DAVID_11.0", name="Microsoft David Desktop")
ZIRA = Voice(id="TTS_MS_EN-US_ZIRA_11.0", name="Microsoft Zira Desktop")
HAZEL = Voice(id="TTS_MS_EN-GB_HAZEL_11.0", name="Microsoft Hazel Desktop")


def test_roles_pick_their_preferred_voices():
    out = assign_voices(["Ada", "Ben"], [DAVID, ZIRA], roles={"Ada": "host", "Ben": "guest"})

    assert out["Ada"].voice_id == DAVID.id
    assert out["Ben"].voice_id == ZIRA.id


def test_speakers_get_distinct_voices_without_role_hints():
    out = assign_voices(["Ada", "Ben"], [DAVID, ZIRA])

    assert out["Ada"].voice_id != out["Ben"].voice_id


def test_repeated_speakers_collapse_to_one_assignment():
    out = assign_voices(["Ada", "Ben", "Ada", "Ben"], [DAVID, ZIRA])

    assert sorted(out) == ["Ada", "Ben"]


def test_assignment_is_deterministic():
    speakers = ["Ada", "Ben", "Cy"]
    voices = [DAVID, ZIRA, HAZEL]
    first = assign_voices(speakers, voices, roles={"Ada": "host"})
    second = assign_voices(speakers, voices, roles={"Ada": "host"})

    assert {s: a.voice_id for s, a in first.items()} == {s: a.voice_id for s, a in second.items()}


def test_more_speakers_than_voices_wraps_around():
    out = assign_voices(["Ada", "Ben"], [DAVID])

    assert out["Ada"].voice_id == DAVID.id
    assert out["Ben"].voice_id == DAVID.id


def test_no_installed_voices_falls_back_to_the_system_default():
    out = assign_voices(["Ada"], [])

    assert out["Ada"].voice_id is None
    assert out["Ada"].voice_name == "system-default"


def test_blank_speaker_names_are_ignored():
    assert set(assign_voices(["", "Ada"], [DAVID])) == {"Ada"}


def test_pace_maps_onto_the_sapi_rate_range():
    assert pace_to_rate(None) == DEFAULT_RATE
    assert pace_to_rate(0) == DEFAULT_RATE
    assert pace_to_rate(150) == 150
    assert pace_to_rate(10) == MIN_RATE
    assert pace_to_rate(9999) == MAX_RATE
