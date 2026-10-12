"""Loop recipes -- the packaged *shapes* of long-running agentic work.

A :class:`Recipe` is the goal-loop working contract specialized for a class of
work: a charter template (the prompt/goal handed to the worker), the domain
events it **suspends** on, and the **resolution** it drives toward. Rendering a
recipe with concrete parameters yields a :class:`RenderedRecipe` -- the fields of
an ordinary ``create`` call -- so a recipe is kickable ad-hoc (coordinator +
worker body + recipe, no standing service required) as readily as it is driven by
a domain service.

See ``visions/plugins/agent-dispatch`` (§Concepts/*The recipe*, §Features/
*loop-recipes* + *recipes-run-ad-hoc*) for the intent.
"""

from __future__ import annotations

from .driver import DriverAction, decide, resolution_outcome
from .registry import (
    EXTERNAL_AUTHOR_CLAUSE,
    REGISTRY,
    RESOLUTION_CLAUSE,
    STAGNATION_CLAUSE,
    SUSPEND_CLAUSE,
    Recipe,
    RecipeError,
    RecipeParam,
    RenderedRecipe,
    UnknownRecipe,
    dedup_key_for,
    get_recipe,
    list_recipes,
    render_recipe,
)

__all__ = [
    "REGISTRY",
    "DriverAction",
    "EXTERNAL_AUTHOR_CLAUSE",
    "RESOLUTION_CLAUSE",
    "Recipe",
    "RecipeError",
    "RecipeParam",
    "RenderedRecipe",
    "STAGNATION_CLAUSE",
    "SUSPEND_CLAUSE",
    "UnknownRecipe",
    "decide",
    "dedup_key_for",
    "get_recipe",
    "list_recipes",
    "render_recipe",
    "resolution_outcome",
]
