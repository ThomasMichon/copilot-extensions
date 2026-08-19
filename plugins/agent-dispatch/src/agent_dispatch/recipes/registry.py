"""Loop recipes -- the packaged *shapes* of long-running agentic work.

A :class:`Recipe` is the goal-loop working contract specialized for a class of
work: a charter template (the prompt/goal handed to the worker), the domain
events it **suspends** on, and the **resolution** it drives toward. Rendering a
recipe with concrete parameters yields a :class:`RenderedRecipe` -- the fields of
an ordinary ``create`` call -- so a recipe is kickable ad-hoc as readily as it is
driven by a standing domain service.

The three first-class archetypes:

* **reviewer** -- drive a change (a pull request) to merged-or-abandoned: review,
  post feedback or approve, suspend on a verdict, resume on update, own the merge.
* **conflict-resolution** -- take the last mile of a PR an automated producer
  opened but nobody is driving: check out its branch, rebase the target in,
  resolve conflicts, force-push back over the same PR, answer review/build
  state, suspend/resume until it lands.
* **goal-driven** -- drive an arbitrary goal against one or more repos through one
  or more pull requests until met or abandoned.

Everything here is pure: no coordinator, no network, no worktree state. The CLI
(``agent-dispatch recipes ...``) turns a rendered recipe into a task.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass, field


class RecipeError(RuntimeError):
    """Base class for recipe problems (unknown recipe, missing parameters)."""


class UnknownRecipe(RecipeError):
    """Requested a recipe name that is not registered."""


@dataclass(frozen=True)
class RecipeParam:
    """One parameter a recipe accepts."""

    name: str
    description: str
    required: bool = True
    default: str | None = None


@dataclass(frozen=True)
class Recipe:
    """A packaged loop archetype.

    Templates are ``str.format``-style with ``{param}`` placeholders drawn from
    the recipe's declared ``params``. ``requires`` are hard capability/identity
    tokens stamped onto the created task; ``labels`` are free-form (the recipe's
    own ``recipe:<name>`` label is added automatically at render time).
    """

    name: str
    summary: str
    params: tuple[RecipeParam, ...]
    title_template: str
    goal_template: str
    done_criteria: str
    charter_template: str
    suspend_on: tuple[str, ...]
    resolution: str
    requires: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()

    def required_params(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.params if p.required)


@dataclass(frozen=True)
class RenderedRecipe:
    """A recipe rendered with concrete parameters -- ready to become a task.

    The field set intentionally mirrors the ``create`` command's inputs so a
    rendered recipe drops straight into the ad-hoc carve (and optional spawn).
    """

    recipe: str
    title: str
    goal: str
    done_criteria: str
    prompt: str
    suspend_on: tuple[str, ...]
    resolution: str
    requires: tuple[str, ...]
    labels: tuple[str, ...]
    params: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "recipe": self.recipe,
            "title": self.title,
            "goal": self.goal,
            "done_criteria": self.done_criteria,
            "prompt": self.prompt,
            "suspend_on": list(self.suspend_on),
            "resolution": self.resolution,
            "requires": list(self.requires),
            "labels": list(self.labels),
            "params": dict(self.params),
        }


# ---- Shared charter language ------------------------------------------------
#
# Every recipe's charter reaffirms the two safety invariants the vision binds
# (drive-the-worktree-to-resolution, no-overlapping-live-workers) so a worker
# embodied from a recipe carries them even in the ad-hoc, no-service path.

_RESOLUTION_CLAUSE = (
    "When you finish -- whether the work landed or you are abandoning it -- drive "
    "your worktree to a clean, resolved state: a merged change, or a workspace "
    "reset to its base branch so nothing is left half-done. If you abandon, say "
    "why and reconcile the source (the change/issue you were sent for) so nothing "
    "downstream believes the work landed. You can do this with "
    "`agent-dispatch resolve --outcome landed|abandoned` (add `--execute` to "
    "perform the unwind). Report what you ultimately did."
)

_SUSPEND_CLAUSE = (
    "You may reach a natural checkpoint where you are waiting on something outside "
    "your control (an update to the change, a review, a build). At such a point, "
    "record your progress and suspend rather than busy-waiting: hand the wait to "
    "the layer with `agent-dispatch run --detach --resume <your-worktree> -- "
    "<blocking-wait-command>`, which tears your session down while a cheap waiter "
    "owns the wait and resumes you -- with your context intact -- when the world "
    "moves."
)


REGISTRY: dict[str, Recipe] = {}


def _register(recipe: Recipe) -> Recipe:
    REGISTRY[recipe.name] = recipe
    return recipe


_register(
    Recipe(
        name="reviewer",
        summary="Drive a pull request to a merged-or-abandoned resolution.",
        params=(
            RecipeParam("repo", "target repo 'owner/name' (or lane) of the change"),
            RecipeParam("pr", "pull-request number/identifier to review"),
            RecipeParam(
                "base", "base branch the change targets", required=False,
                default="the default branch",
            ),
        ),
        title_template="review {repo}#{pr} to resolution",
        goal_template="Drive pull request {repo}#{pr} to a merged or abandoned resolution.",
        done_criteria=(
            "The pull request is merged, or it is closed/abandoned with the reason "
            "recorded. A verdict alone is not done -- you own it until it resolves."
        ),
        charter_template=(
            "You are reviewing pull request {repo}#{pr} (base {base}). Work with the "
            "full target-repo source (a local checkout, container, or codespace) and "
            "act through the repo's own review/merge tools.\n\n"
            "Loop: read the change, post specific feedback or approve, and drive it "
            "toward merge. " + _SUSPEND_CLAUSE + " When the change updates, resume and "
            "re-review only what moved. Take ownership of the merge when the change is "
            "ready and it is yours to land.\n\n" + _RESOLUTION_CLAUSE
        ),
        suspend_on=("change-updated", "review-posted"),
        resolution="pull-request-merged-or-abandoned",
        requires=(),
        labels=("kind:review",),
    )
)

_register(
    Recipe(
        name="conflict-resolution",
        summary=(
            "Drive a producer-opened PR the last mile: check out, rebase, "
            "resolve conflicts, force-push back over the same PR, land."
        ),
        params=(
            RecipeParam("repo", "target repo 'owner/name' (or lane) of the change"),
            RecipeParam("pr", "pull-request number/identifier that is stuck"),
            RecipeParam(
                "base", "base branch to rebase onto", required=False,
                default="the default branch",
            ),
        ),
        title_template="unstick {repo}#{pr}",
        goal_template="Take {repo}#{pr} the last mile to a mergeable, merged state.",
        done_criteria=(
            "The change is rebased clean, its review/build state is satisfied, and it "
            "is merged -- or it is abandoned with the blocker recorded."
        ),
        charter_template=(
            "Pull request {repo}#{pr} was opened by an automated producer and is now "
            "stuck: it has merge conflicts against {base} and nobody is driving it. "
            "Take the last mile to a mergeable state -- check out its branch into a "
            "local worktree, rebase (or merge) {base} in, resolve the conflicts, and "
            "**force-push the resolved branch back over the PR head** so the same PR "
            "updates in place -- never open a second PR. Then answer its review and "
            "build state.\n\n" + _SUSPEND_CLAUSE + " Resume on the next "
            "review/build/update and iterate until it lands.\n\n"
            "Stay within the intent of the existing change -- you are unblocking it, not "
            "redesigning it.\n\n" + _RESOLUTION_CLAUSE
        ),
        suspend_on=("change-updated", "build-updated", "review-posted"),
        resolution="pull-request-merged-or-abandoned",
        requires=(),
        labels=("kind:conflict-resolution",),
    )
)

_register(
    Recipe(
        name="goal-driven",
        summary="Drive an arbitrary goal against one or more repos through PRs.",
        params=(
            RecipeParam("goal", "the objective to pursue"),
            RecipeParam(
                "repos", "target repo(s) the work lands in", required=False,
                default="the appropriate target repo(s)",
            ),
        ),
        title_template="drive: {goal}",
        goal_template="{goal}",
        done_criteria=(
            "The stated goal is met and its change(s) are merged, or the goal is "
            "abandoned with the reason recorded."
        ),
        charter_template=(
            "Goal: {goal}\n\n"
            "Drive this to completion against {repos} through one or more pull "
            "requests, handling conflicts and review feedback as they arise. Stay "
            "within the bounds of the goal -- do not expand scope.\n\n"
            + _SUSPEND_CLAUSE + " Resume when a change you opened moves.\n\n"
            + _RESOLUTION_CLAUSE
        ),
        suspend_on=("change-updated", "review-posted"),
        resolution="goal-met-or-abandoned",
        requires=(),
        labels=("kind:goal",),
    )
)


_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def list_recipes() -> list[Recipe]:
    """All registered recipes, ordered by name."""
    return [REGISTRY[name] for name in sorted(REGISTRY)]


def get_recipe(name: str) -> Recipe:
    """Look up a recipe by name, or raise :class:`UnknownRecipe`."""
    try:
        return REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(REGISTRY)) or "(none)"
        raise UnknownRecipe(f"unknown recipe {name!r}; known: {known}") from None


def _fill(template: str, values: dict[str, str]) -> str:
    """``str.format``-style fill that leaves an unknown ``{placeholder}`` intact
    rather than raising -- required params are validated separately, and prose in
    a charter may legitimately contain braces."""

    class _Safe(dict):
        def __missing__(self, key: str) -> str:  # pragma: no cover - via format_map
            return "{" + key + "}"

    return string.Formatter().vformat(template, (), _Safe(values))


def render_recipe(name: str, params: dict[str, str]) -> RenderedRecipe:
    """Render ``name`` with ``params`` into a :class:`RenderedRecipe`.

    Applies declared defaults, then validates that every required parameter is
    present (raising :class:`RecipeError` listing the missing ones). Unknown
    parameters are ignored (forward-compatible).
    """
    recipe = get_recipe(name)

    values: dict[str, str] = {}
    for p in recipe.params:
        if p.default is not None:
            values[p.name] = p.default
    values.update({k: str(v) for k, v in params.items() if v is not None})

    missing = [p.name for p in recipe.params if p.required and not values.get(p.name)]
    if missing:
        raise RecipeError(
            f"recipe {name!r} is missing required parameter(s): {', '.join(missing)}"
        )

    labels = tuple(dict.fromkeys((f"recipe:{recipe.name}", *recipe.labels)))
    return RenderedRecipe(
        recipe=recipe.name,
        title=_fill(recipe.title_template, values),
        goal=_fill(recipe.goal_template, values),
        done_criteria=_fill(recipe.done_criteria, values),
        prompt=_fill(recipe.charter_template, values),
        suspend_on=recipe.suspend_on,
        resolution=recipe.resolution,
        requires=recipe.requires,
        labels=labels,
        params={k: values[k] for k in values},
    )


def dedup_key_for(rendered: RenderedRecipe) -> str:
    """A reserved-work dedup key derived from the recipe + its (effective)
    parameters, so re-kicking the same recipe for the same target **collides**
    rather than forking the work -- the dedup-before-create half of the
    *no-overlapping-live-workers* invariant. Single source for the CLI and MCP
    kick paths.
    """
    parts = ":".join(f"{k}={rendered.params[k]}" for k in sorted(rendered.params))
    return f"recipe:{rendered.recipe}:{parts}"
