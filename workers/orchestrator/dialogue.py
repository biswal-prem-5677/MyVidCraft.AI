from __future__ import annotations

from packages.core.myvidcraft_core.llm_schemas import LLMDialogueScript, LLMEpisodePlan
from packages.core.myvidcraft_core.schemas import (
    CharacterProfile,
    DialogueMode,
    DialogueTurn,
    EpisodeBeat,
    EpisodePlan,
)
from packages.prompts import load as load_prompt
from packages.prompts import render as render_prompt

from . import llm

TARGET_MINUTES = 6


def _mode_guidance(mode: DialogueMode) -> str:
    """Per-mode direction. One prompt file per mode, named after the enum value."""
    return load_prompt(f"dialogue/mode_{mode.value}")


# --------------------------------------------------------------------------
# Episode plan
# --------------------------------------------------------------------------


def generate_episode_plan(niche: str, mode: DialogueMode, brand_profile: dict) -> EpisodePlan:
    """Design the episode blueprint. Falls back to a template if the LLM is unavailable."""
    plan = _generate_episode_plan_llm(niche, mode, brand_profile)
    if plan is not None:
        return plan
    return _generate_episode_plan_template(niche, mode, brand_profile)


def _constraints(mode: DialogueMode) -> dict:
    return {"target_minutes": TARGET_MINUTES, "mode": mode.value}


def _generate_episode_plan_llm(niche: str, mode: DialogueMode, brand_profile: dict) -> EpisodePlan | None:
    host_tone = str(brand_profile.get("host_tone") or brand_profile.get("tone") or "calm")
    guest_tone = str(brand_profile.get("guest_tone") or "confident")
    catchphrase = brand_profile.get("catchphrase")
    taboos = brand_profile.get("taboos") or []

    user = (
        f"Niche: {niche}\n"
        f"Dialogue mode: {mode.value}\n"
        f"{_mode_guidance(mode)}\n"
        f"Host tone: {host_tone}\n"
        f"Guest tone: {guest_tone}\n"
        f"Target runtime: about {TARGET_MINUTES} minutes.\n"
        + (f"Host catchphrase to work in naturally: {catchphrase}\n" if catchphrase else "")
        + (f"Forbidden topics: {', '.join(map(str, taboos))}\n" if taboos else "")
        + "\nDesign the episode blueprint."
    )

    result = llm.generate_structured(
        system=render_prompt("dialogue/plan.system"),
        user=user,
        output_format=LLMEpisodePlan,
    )
    if result is None:
        return None
    # A two-speaker show needs exactly two speakers; anything else breaks voice
    # assignment and the stitcher downstream.
    if len(result.characters) != 2 or not result.beats:
        return None
    roles = {c.role for c in result.characters}
    if roles != {"host", "guest"}:
        return None

    return result.to_domain(mode=mode, constraints=_constraints(mode))


def _generate_episode_plan_template(niche: str, mode: DialogueMode, brand_profile: dict) -> EpisodePlan:
    host_tone = str(brand_profile.get("host_tone") or brand_profile.get("tone") or "calm")
    guest_tone = str(brand_profile.get("guest_tone") or "confident")

    characters = [
        CharacterProfile(
            name="Host",
            role="host",
            tone=host_tone,
            rhetoric="teacher" if mode == DialogueMode.teaching else "debater",
            catchphrases=[str(brand_profile.get("catchphrase") or "Let's make this practical.")],
            humor_level=max(0, min(10, int(brand_profile.get("humor_level") or 4))),
        ),
        CharacterProfile(
            name="Guest",
            role="guest",
            tone=guest_tone,
            rhetoric="storyteller" if mode == DialogueMode.storytelling else "expert",
            humor_level=max(0, min(10, int(brand_profile.get("guest_humor_level") or 3))),
        ),
    ]

    beats = [
        EpisodeBeat(title="Cold open hook", goal="Create curiosity gap + promise payoff", intended_emotion="curious"),
        EpisodeBeat(title="Core framework", goal="Explain the model in 3 parts", intended_emotion="clarity"),
        EpisodeBeat(title="Contrarian insight", goal="Introduce a tension or debate point", intended_emotion="surprised"),
        EpisodeBeat(title="Action steps", goal="Deliver 3 concrete steps", intended_emotion="motivated"),
        EpisodeBeat(title="Recap + CTA", goal="Summarize and ask for follow/share", intended_emotion="confident"),
    ]

    return EpisodePlan(
        title=f"{niche}: the playbook most people miss",
        premise=f"A fast, high-signal breakdown of {niche} with a concrete framework and action steps.",
        stance=f"Most advice on {niche} is noisy; the winning approach is systems + feedback loops.",
        mode=mode,
        beats=beats,
        characters=characters,
        constraints=_constraints(mode),
    )


# --------------------------------------------------------------------------
# Dialogue
# --------------------------------------------------------------------------


def generate_dialogue(
    niche: str,
    mode: DialogueMode,
    brand_profile: dict,
    plan: EpisodePlan | None = None,
) -> list[DialogueTurn]:
    """
    Write the spoken dialogue.

    `plan` is passed so the script actually follows the blueprint's beats and uses
    its character names — without it the two artifacts describe different episodes.
    """
    turns = _generate_dialogue_llm(niche, mode, brand_profile, plan)
    if turns:
        return turns
    return _generate_dialogue_template(niche, mode, brand_profile, plan)


def _speaker_names(plan: EpisodePlan | None) -> tuple[str, str]:
    if plan and len(plan.characters) == 2:
        host = next((c.name for c in plan.characters if c.role == "host"), plan.characters[0].name)
        guest = next((c.name for c in plan.characters if c.role == "guest"), plan.characters[1].name)
        return host, guest
    return "Host", "Guest"


def _generate_dialogue_llm(
    niche: str,
    mode: DialogueMode,
    brand_profile: dict,
    plan: EpisodePlan | None,
) -> list[DialogueTurn]:
    host_name, guest_name = _speaker_names(plan)

    lines = [
        f"Niche: {niche}",
        f"Dialogue mode: {mode.value}",
        _mode_guidance(mode),
        "",
        f"Speaker names (use these EXACTLY): {host_name} (host), {guest_name} (guest)",
    ]

    if plan:
        lines += [
            "",
            f"Episode title: {plan.title}",
            f"Premise: {plan.premise}",
            f"Stance: {plan.stance}",
            "",
            "Follow these beats in order:",
        ]
        lines += [f"  {i}. {b.title} — {b.goal} (feel: {b.intended_emotion})" for i, b in enumerate(plan.beats, 1)]
        for c in plan.characters:
            detail = f"  {c.name} ({c.role}): tone {c.tone}, style {c.rhetoric}, humor {c.humor_level}/10"
            if c.catchphrases:
                detail += f", catchphrases: {'; '.join(c.catchphrases)}"
            lines.append(detail)

    if brand_profile.get("taboos"):
        lines.append(f"Forbidden topics: {', '.join(map(str, brand_profile['taboos']))}")

    lines += [
        "",
        f"Write about {TARGET_MINUTES} minutes of speech: roughly 16 to 24 turns, "
        "alternating speakers but not rigidly. Return the full script.",
    ]

    result = llm.generate_structured(
        system=render_prompt("dialogue/script.system"),
        user="\n".join(lines),
        output_format=LLMDialogueScript,
    )
    if result is None:
        return []

    turns = result.to_domain()
    if len(turns) < 4:
        return []

    # The model occasionally drifts on speaker names; snap anything unrecognised
    # back onto the two real speakers so voice assignment stays deterministic.
    allowed = {host_name.lower(): host_name, guest_name.lower(): guest_name}
    for i, turn in enumerate(turns):
        if turn.speaker.lower() not in allowed:
            turn.speaker = host_name if i % 2 == 0 else guest_name
        else:
            turn.speaker = allowed[turn.speaker.lower()]
    return turns


def _generate_dialogue_template(
    niche: str,
    mode: DialogueMode,
    brand_profile: dict,
    plan: EpisodePlan | None = None,
) -> list[DialogueTurn]:
    """Offline fallback. Deterministic for a given niche + mode."""
    host, guest = _speaker_names(plan)
    catchphrase = str(brand_profile.get("catchphrase") or "Let's make this practical.")
    spicy = bool(brand_profile.get("spicy") or mode == DialogueMode.debate)

    turns: list[DialogueTurn] = [
        DialogueTurn(
            speaker=host,
            text=f"Stop scrolling. If you care about {niche}, this will change how you think. {catchphrase}",
            emotion="excited",
            pace_wpm=165,
            pause_after_ms=600,
            broll_cues=[f"Big on-screen text: '{niche} PLAYBOOK'"],
        ),
        DialogueTurn(
            speaker=guest,
            text=f"Here's the truth: most people treat {niche} like tips. The winners treat it like a system.",
            emotion="confident",
            pace_wpm=155,
            pause_after_ms=500,
            broll_cues=["Simple diagram: Inputs, Process, Feedback, Results"],
        ),
        DialogueTurn(
            speaker=host,
            text="Give them the three part framework, clean and usable.",
            emotion="neutral",
            pace_wpm=150,
            pause_after_ms=350,
        ),
        DialogueTurn(
            speaker=guest,
            text="Part one: pick one metric. Part two: ship small iterations daily. "
            "Part three: review weekly and cut what doesn't move the metric.",
            emotion="teaching",
            pace_wpm=150,
            pause_after_ms=650,
            broll_cues=["On-screen bullets: Metric / Daily ship / Weekly review"],
        ),
    ]

    if spicy:
        turns.append(
            DialogueTurn(
                speaker=host,
                text=f"Hot take: most {niche} advice is entertainment, not results. Agree?",
                emotion="provocative",
                pace_wpm=165,
                pause_after_ms=550,
            )
        )
        turns.append(
            DialogueTurn(
                speaker=guest,
                text="Mostly. If it doesn't change behavior today, it's just noise. "
                "The algorithm rewards clarity and repetition, not complexity.",
                emotion="assertive",
                pace_wpm=160,
                pause_after_ms=650,
            )
        )

    turns.append(
        DialogueTurn(
            speaker=host,
            text=f"Your move: comment the word SYSTEM and I'll send a checklist for {niche}. "
            "Follow, save, and share this with someone who keeps overthinking.",
            emotion="confident",
            pace_wpm=160,
            pause_after_ms=500,
        )
    )

    return turns
