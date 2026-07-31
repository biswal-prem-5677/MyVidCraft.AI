"""
Prompts as source code.

Prompt text used to live in Python string constants, where it was the
highest-variance and least-reviewable part of the system: a wording change
showed up in a diff surrounded by code, and nothing verified that a template's
placeholders matched what the caller actually passed.

Here each prompt is a file under this package, addressed by its path without the
extension (`brain/strategy.system`). Two properties matter:

1. **Required parameters are derived from the template**, not declared
   separately, so they cannot drift out of sync with the text.
2. **`render()` is strict.** A missing or unexpected parameter raises rather
   than silently producing a prompt with a literal `{days}` in it — which the
   model would happily answer, badly.

Templates use `str.format` syntax. A literal brace in prompt text must be
doubled (`{{`).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from string import Formatter

PROMPTS_DIR = Path(__file__).parent
SUFFIX = ".md"


class PromptNotFoundError(KeyError):
    """Raised when a prompt name does not resolve to a file."""


class PromptParamsError(ValueError):
    """Raised when render() is given the wrong parameters for a template."""


def available() -> list[str]:
    """Every prompt name in this package, sorted."""
    return sorted(
        p.relative_to(PROMPTS_DIR).with_suffix("").as_posix()
        for p in PROMPTS_DIR.rglob(f"*{SUFFIX}")
    )


def _path(name: str) -> Path:
    # Resolve and confine: a name like "../../secrets" must not escape the package.
    candidate = (PROMPTS_DIR / f"{name}{SUFFIX}").resolve()
    if PROMPTS_DIR.resolve() not in candidate.parents:
        raise PromptNotFoundError(f"prompt name escapes the prompts package: {name!r}")
    if not candidate.is_file():
        raise PromptNotFoundError(f"no prompt named {name!r} (have: {', '.join(available())})")
    return candidate


@lru_cache(maxsize=None)
def load(name: str) -> str:
    """The raw template text, trailing whitespace stripped. Cached."""
    return _path(name).read_text(encoding="utf-8").rstrip()


@lru_cache(maxsize=None)
def required_params(name: str) -> frozenset[str]:
    """
    Placeholder names the template expects, derived from the text itself.

    Positional placeholders (`{}`) are rejected: prompts are long and edited by
    hand, so every substitution must be named to stay reviewable.
    """
    found: set[str] = set()
    for _, field, _, _ in Formatter().parse(load(name)):
        if field is None:
            continue
        if field == "" or field.isdigit():
            raise PromptParamsError(f"prompt {name!r} uses a positional placeholder; name it instead")
        found.add(field.split(".")[0].split("[")[0])
    return frozenset(found)


def render(name: str, **params: object) -> str:
    """
    Render a prompt. Raises if `params` does not exactly match the template.

    Strict in both directions on purpose: a missing parameter leaves a literal
    placeholder in the prompt, and an unexpected one usually means the template
    was edited without the caller being updated.
    """
    required = required_params(name)
    given = set(params)

    if missing := required - given:
        raise PromptParamsError(f"prompt {name!r} is missing parameter(s): {', '.join(sorted(missing))}")
    if unexpected := given - required:
        raise PromptParamsError(f"prompt {name!r} got unexpected parameter(s): {', '.join(sorted(unexpected))}")

    return load(name).format(**params)
