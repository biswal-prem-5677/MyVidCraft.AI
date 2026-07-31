"""The prompt loader, and a contract check over the shipped prompts."""

from __future__ import annotations

import pytest

from packages.core.myvidcraft_core.schemas import DialogueMode
from packages.prompts import (
    PromptNotFoundError,
    PromptParamsError,
    available,
    load,
    render,
    required_params,
)


def test_every_shipped_prompt_renders():
    """
    Guards the whole prompt directory: a file whose placeholders cannot be
    satisfied fails here rather than at the first paid API call.
    """
    assert available(), "no prompts found"
    for name in available():
        rendered = render(name, **{p: "x" for p in required_params(name)})
        assert rendered.strip()
        assert "{" not in rendered, f"{name} still has an unsubstituted placeholder"


def test_required_params_are_derived_from_the_text():
    assert required_params("brain/strategy.system") == {"days"}
    assert required_params("dialogue/script.system") == frozenset()


def test_render_substitutes():
    out = render("brain/strategy.system", days=7)

    assert "exactly 7" in out
    assert "{days}" not in out


def test_missing_param_raises():
    with pytest.raises(PromptParamsError, match="missing parameter"):
        render("brain/strategy.system")


def test_unexpected_param_raises():
    """Usually means the template was edited without updating the caller."""
    with pytest.raises(PromptParamsError, match="unexpected parameter"):
        render("dialogue/plan.system", days=7)


def test_unknown_prompt_raises():
    with pytest.raises(PromptNotFoundError):
        load("brain/does-not-exist")


def test_name_cannot_escape_the_package():
    with pytest.raises(PromptNotFoundError):
        load("../../../../etc/passwd")


def test_every_dialogue_mode_has_a_guidance_prompt():
    """`dialogue._mode_guidance` builds its name from the enum value."""
    for mode in DialogueMode:
        assert load(f"dialogue/mode_{mode.value}").strip()


def test_engines_resolve_their_prompts():
    """The two engines' prompt names must exist, since they are built as strings."""
    from workers.orchestrator import dialogue

    for mode in DialogueMode:
        assert dialogue._mode_guidance(mode).strip()
