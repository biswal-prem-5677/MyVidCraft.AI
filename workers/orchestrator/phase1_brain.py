from __future__ import annotations

import hashlib
from datetime import date, timedelta

from packages.core.zentcast_core.schemas import ContentCalendarItem, Platform, StrategyPack


def _stable_float_0_1(seed: str) -> float:
    h = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def generate_strategy(niche: str, brand_profile: dict) -> StrategyPack:
    brand_tone = str(brand_profile.get("tone") or brand_profile.get("style") or "confident")
    brand_summary = f"{brand_tone} personal brand in {niche}"

    topic_templates = [
        f"What nobody tells you about {niche}",
        f"The uncomfortable truth about {niche}",
        f"Do this for 7 days in {niche} — results",
        f"{niche}: 5 mistakes beginners keep repeating",
        f"The fastest way to improve your {niche} outcomes",
    ]
    hook_library = [
        f"Stop scrolling — this changes how you think about {niche}.",
        f"If you’re serious about {niche}, you need to hear this.",
        f"This is why most people fail at {niche}.",
        f"The 10-second test that predicts your {niche} results.",
        f"Do NOT do this in {niche} — do this instead.",
    ]

    today = date.today()
    calendar: list[ContentCalendarItem] = []
    platforms = [Platform.youtube, Platform.podcast, Platform.youtube_shorts]
    for i in range(7):
        topic = topic_templates[i % len(topic_templates)]
        hook = hook_library[i % len(hook_library)]
        platform = platforms[i % len(platforms)]
        hook_score = 0.55 + 0.4 * _stable_float_0_1(f"{niche}:{hook}:{i}")
        calendar.append(
            ContentCalendarItem(
                date=str(today + timedelta(days=i)),
                platform=platform,
                topic=topic,
                hook=hook,
                format="talking_head" if platform != Platform.podcast else "audio_episode",
                cta="Follow for part 2 / save this",
                asset_prompts=[f"Minimal bold text thumbnail: '{topic[:28]}…'"],
                hook_score=min(1.0, max(0.0, hook_score)),
            )
        )

    return StrategyPack(
        niche=niche,
        brand_summary=brand_summary,
        viral_topic_slate=topic_templates,
        hook_library=hook_library,
        calendar=calendar,
        notes=[
            "Phase 1 strategy is heuristic (fast).",
            "Later phases will plug in trend sources + learned scoring.",
        ],
    )

