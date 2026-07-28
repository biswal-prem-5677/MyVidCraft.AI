from __future__ import annotations

from packages.core.myvidcraft_core.schemas import (
    CharacterProfile,
    DialogueMode,
    DialogueTurn,
    EpisodeBeat,
    EpisodePlan,
)


def generate_episode_plan(niche: str, mode: DialogueMode, brand_profile: dict) -> EpisodePlan:
    host_tone = str(brand_profile.get("host_tone") or brand_profile.get("tone") or "calm")
    guest_tone = str(brand_profile.get("guest_tone") or "confident")

    characters = [
        CharacterProfile(
            name="Host",
            role="host",
            tone=host_tone,
            rhetoric="teacher" if mode == DialogueMode.teaching else "debater",
            catchphrases=[str(brand_profile.get("catchphrase") or "Let’s make this practical.")],
            humor_level=int(brand_profile.get("humor_level") or 4),
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

    title = f"{niche}: the playbook most people miss"
    premise = f"A fast, high-signal breakdown of {niche} with a concrete framework and action steps."
    stance = f"Most advice on {niche} is noisy; the winning approach is systems + feedback loops."

    return EpisodePlan(
        title=title,
        premise=premise,
        stance=stance,
        mode=mode,
        beats=beats,
        characters=characters,
        constraints={"phase": 1, "target_minutes": 6},
    )


def generate_dialogue(niche: str, mode: DialogueMode, brand_profile: dict) -> list[DialogueTurn]:
    catchphrase = str(brand_profile.get("catchphrase") or "Let’s make this practical.")
    spicy = bool(brand_profile.get("spicy") or mode == DialogueMode.debate)

    turns: list[DialogueTurn] = [
        DialogueTurn(
            speaker="Host",
            text=f"Stop scrolling. If you care about {niche}, this will change how you think. {catchphrase}",
            emotion="excited",
            pace_wpm=165,
            pause_after_ms=600,
            broll_cues=[f"Big on-screen text: '{niche} PLAYBOOK'"],
        ),
        DialogueTurn(
            speaker="Guest",
            text=f"Here’s the truth: most people treat {niche} like tips. The winners treat it like a system.",
            emotion="confident",
            pace_wpm=155,
            pause_after_ms=500,
            broll_cues=["Simple diagram: Inputs → Process → Feedback → Results"],
        ),
        DialogueTurn(
            speaker="Host",
            text="Give them the 3-part framework, clean and usable.",
            emotion="neutral",
            pace_wpm=150,
            pause_after_ms=350,
        ),
        DialogueTurn(
            speaker="Guest",
            text="Part 1: pick one metric. Part 2: ship small iterations daily. Part 3: review weekly and cut what doesn’t move the metric.",
            emotion="teaching",
            pace_wpm=150,
            pause_after_ms=650,
            broll_cues=["On-screen bullets: Metric / Daily ship / Weekly review"],
        ),
    ]

    if spicy:
        turns.append(
            DialogueTurn(
                speaker="Host",
                text=f"Hot take: 90% of {niche} advice is entertainment, not results. Agree?",
                emotion="provocative",
                pace_wpm=165,
                pause_after_ms=550,
            )
        )
        turns.append(
            DialogueTurn(
                speaker="Guest",
                text="Mostly. If it doesn’t change behavior today, it’s just noise. The algorithm rewards clarity and repetition, not complexity.",
                emotion="assertive",
                pace_wpm=160,
                pause_after_ms=650,
            )
        )

    closing = (
        f"Your move: comment 'SYSTEM' and I’ll send a checklist for {niche}. "
        "Follow, save, and share this with someone who keeps overthinking."
    )
    turns.append(
        DialogueTurn(
            speaker="Host",
            text=closing,
            emotion="confident",
            pace_wpm=160,
            pause_after_ms=500,
        )
    )

    return turns

