import pytest
from pydantic import ValidationError

from packages.core.myvidcraft_core.schemas import (
    ArtifactType,
    CharacterProfile,
    DialogueMode,
    DialogueTurn,
    EpisodeBeat,
    EpisodePlan,
    RunCreate,
    VoiceRerunCreate,
)


def test_dialogue_turn_defaults_are_tts_ready():
    turn = DialogueTurn(speaker="Ada", text="Hello.")

    assert turn.pace_wpm == 150
    assert turn.pause_after_ms == 400
    assert turn.broll_cues == []


@pytest.mark.parametrize("pace", [10, 500])
def test_pace_outside_the_speakable_range_is_rejected(pace):
    with pytest.raises(ValidationError):
        DialogueTurn(speaker="Ada", text="Hi", pace_wpm=pace)


def test_pause_cannot_be_negative():
    with pytest.raises(ValidationError):
        DialogueTurn(speaker="Ada", text="Hi", pause_before_ms=-1)


def test_episode_plan_round_trips_through_json():
    plan = EpisodePlan(
        title="Why your notes app is lying to you",
        premise="Tooling is not a system.",
        stance="contrarian",
        mode=DialogueMode.debate,
        beats=[EpisodeBeat(title="Setup", goal="Frame the problem")],
        characters=[
            CharacterProfile(name="Ada", role="host"),
            CharacterProfile(name="Ben", role="guest"),
        ],
    )

    restored = EpisodePlan.model_validate_json(plan.model_dump_json())

    assert restored == plan
    assert [c.role for c in restored.characters] == ["host", "guest"]


def test_character_role_is_constrained():
    with pytest.raises(ValidationError):
        CharacterProfile(name="Ada", role="narrator")


def test_phase2_artifact_types_exist():
    values = {a.value for a in ArtifactType}

    assert {
        "turn_audio_wav",
        "turn_audio_manifest",
        "episode_audio_wav",
        "episode_audio_mp3",
    } <= values


def test_run_create_defaults_to_teaching_mode():
    assert RunCreate().mode is DialogueMode.teaching


def test_voice_rerun_body_is_optional():
    assert VoiceRerunCreate().voice_overrides == {}
    assert VoiceRerunCreate(voice_overrides={"Ada": "VOICE_ID"}).voice_overrides == {"Ada": "VOICE_ID"}
