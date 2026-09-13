"""``recipes`` CLI command family, extracted from ``__main__.py``.

A recipe is a packaged loop archetype (reviewer / conflict-resolution /
goal-driven). ``recipes list|describe|render`` are pure introspection;
``recipes kick`` renders a recipe into an ordinary task and reuses
``_cmd_create`` (so the same dedup / spawn / lane resolution applies); ``recipes
drive`` decides the next loop step for a recipe given a ``--signal`` (the
driver's executable rhythm). See visions/plugins/agent-dispatch
(SS Concepts/*The recipe*, SS Features/*loop-recipes* + *recipes-run-ad-hoc*).

Split out to keep ``__main__.py`` under its module-size ceiling (see
``tools/check-module-size.py``) rather than growing an already very large
file further -- this is purely a move, no behavior change.

This module still needs a handful of names that genuinely belong to
``__main__.py`` (``_emit``, ``_cmd_create``, ``_run_resolution_step``,
``_spawn_detached_waiter``) -- CLI-wide helpers other commands there (``_cmd_run``,
``_cmd_resolve``) use too, monkeypatched by tests via their
``agent_dispatch.__main__`` attribute path. ``loop_commands._resolve_cli_module()``
already solves resolving the actually-running ``__main__`` module (``python -m
agent_dispatch`` loads it as ``sys.modules["__main__"]``, never as
``sys.modules["agent_dispatch.__main__"]``); reuse it here via the same
``_proxy()`` pattern instead of duplicating the resolution logic.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from .loop_commands import _resolve_cli_module


def _proxy(name: str):
    """Delegate to ``agent_dispatch.__main__.<name>`` via ``_resolve_cli_module``."""

    def _fn(*args, **kwargs):
        return getattr(_resolve_cli_module(), name)(*args, **kwargs)

    return _fn


_emit = _proxy("_emit")
_cmd_create = _proxy("_cmd_create")
_run_resolution_step = _proxy("_run_resolution_step")
_spawn_detached_waiter = _proxy("_spawn_detached_waiter")


def _parse_recipe_params(pairs: list[str] | None) -> dict[str, str]:
    """Parse repeated ``--param KEY=VALUE`` into a dict."""
    out: dict[str, str] = {}
    for item in pairs or []:
        if "=" not in item:
            raise ValueError(f"--param must be KEY=VALUE, got {item!r}")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"--param has an empty key: {item!r}")
        out[key] = value
    return out


def _recipe_param_dicts(recipe: Any) -> list[dict]:
    return [
        {
            "name": p.name,
            "required": p.required,
            "default": p.default,
            "description": p.description,
        }
        for p in recipe.params
    ]


def _cmd_recipes_list(args: argparse.Namespace) -> int:
    from .recipes import list_recipes

    return _emit(
        [
            {
                "name": r.name,
                "summary": r.summary,
                "params": _recipe_param_dicts(r),
                "suspend_on": list(r.suspend_on),
                "resolution": r.resolution,
            }
            for r in list_recipes()
        ]
    )


def _cmd_recipes_describe(args: argparse.Namespace) -> int:
    from .recipes import UnknownRecipe, get_recipe

    try:
        r = get_recipe(args.name)
    except UnknownRecipe as exc:
        print(f"agent-dispatch: {exc}", file=sys.stderr)
        return 2
    return _emit(
        {
            "name": r.name,
            "summary": r.summary,
            "params": _recipe_param_dicts(r),
            "title_template": r.title_template,
            "goal_template": r.goal_template,
            "done_criteria": r.done_criteria,
            "charter_template": r.charter_template,
            "suspend_on": list(r.suspend_on),
            "resolution": r.resolution,
            "requires": list(r.requires),
            "labels": list(r.labels),
        }
    )


def _cmd_recipes_render(args: argparse.Namespace) -> int:
    from .recipes import RecipeError, render_recipe

    try:
        rendered = render_recipe(args.name, _parse_recipe_params(args.param))
    except (RecipeError, ValueError) as exc:
        print(f"agent-dispatch: {exc}", file=sys.stderr)
        return 2
    return _emit(rendered.to_dict())


def _recipe_dedup_key(rendered: Any) -> str:
    """A reserved-work dedup key so re-kicking the same recipe+params collides
    rather than forking the work (the *no-overlapping-live-workers* invariant's
    dedup-before-create half). Delegates to the shared registry helper so the CLI
    and MCP kick paths derive the same key."""
    from .recipes import dedup_key_for

    return dedup_key_for(rendered)


def _recipe_create_namespace(args: argparse.Namespace, rendered: Any) -> argparse.Namespace:
    """Build a ``create``-shaped namespace from a rendered recipe so ``kick`` can
    reuse ``_cmd_create`` verbatim (dedup, spawn, lane resolution)."""
    return argparse.Namespace(
        # recipe-derived
        title=rendered.title,
        prompt=rendered.prompt,
        goal=rendered.goal,
        done_criteria=rendered.done_criteria,
        require=list(rendered.requires) or None,
        label=list(dict.fromkeys([*rendered.labels, *(getattr(args, "label", None) or [])])),
        dedup_key=getattr(args, "dedup_key", None) or _recipe_dedup_key(rendered),
        source="recipe",
        origin_ref=rendered.recipe,
        evaluator_ref=None,
        # spawn passthrough (a recipe worker wants a full checkout -> embody body)
        spawn=getattr(args, "spawn", False),
        spawn_backend=getattr(args, "spawn_backend", "embody"),
        spawn_agent=getattr(args, "spawn_agent", "task-worker"),
        run_async=getattr(args, "run_async", False),
        verify_timeout=getattr(args, "verify_timeout", 0),
        # lane / client passthrough
        repo=getattr(args, "repo", None),
        url=getattr(args, "url", None),
        token=getattr(args, "token", None),
        # create knobs left at their defaults (a recipe kick uses none of these)
        proposed=False,
        claim=False,
        exclude=None,
        affinity=None,
        payload_ref=None,
        payload_inline=None,
        payload_file=None,
        target_machine=None,
        target_worktree=None,
        target_repo=None,
        not_before=0.0,
        machine=getattr(args, "machine", None),
        worktree=getattr(args, "worktree", None),
    )


def _cmd_recipes_kick(args: argparse.Namespace) -> int:
    from .recipes import RecipeError, render_recipe

    try:
        rendered = render_recipe(args.name, _parse_recipe_params(args.param))
    except (RecipeError, ValueError) as exc:
        print(f"agent-dispatch: {exc}", file=sys.stderr)
        return 2

    create_ns = _recipe_create_namespace(args, rendered)
    if getattr(args, "dry_run", False):
        preview = rendered.to_dict()
        preview.update(
            {
                "dry_run": True,
                "dedup_key": create_ns.dedup_key,
                "spawn": create_ns.spawn,
                "spawn_backend": create_ns.spawn_backend,
            }
        )
        return _emit(preview)
    return _cmd_create(create_ns)


def _cmd_recipes_drive(args: argparse.Namespace) -> int:
    """Decide the next loop step for a recipe given a ``--signal`` (the driver's
    executable rhythm). Prints the action; ``--execute`` performs the SUSPEND
    (detached hibernation wait) and RESOLVE (drive-to-resolution) legs -- WORK is
    the agent's own to do."""
    from .recipes import UnknownRecipe, decide, get_recipe
    from .recipes.driver import RESOLVE, SUSPEND

    try:
        recipe = get_recipe(args.name)
    except UnknownRecipe as exc:
        print(f"agent-dispatch: {exc}", file=sys.stderr)
        return 2

    action = decide(recipe, args.signal)
    report: dict[str, Any] = {
        "recipe": recipe.name,
        "signal": args.signal,
        "action": action.to_dict(),
    }

    if not args.execute:
        return _emit(report)

    if action.kind == SUSPEND:
        wait_cmd = getattr(args, "_dashdash_tail", None)
        if wait_cmd is None:
            wait_cmd = list(args.wait_cmd or [])
            if wait_cmd and wait_cmd[0] == "--":
                wait_cmd = wait_cmd[1:]
        if not wait_cmd or not args.resume:
            report["executed"] = False
            report["note"] = (
                "SUSPEND needs --resume <worktree> and a wait command after '--' "
                "to hand off; nothing executed"
            )
            return _emit(report)
        from .hibernation import RunSpec

        spec = RunSpec(command=tuple(wait_cmd), resume_worktree=args.resume, task_id=args.task)
        report["executed"] = True
        report["waiter"] = _spawn_detached_waiter(spec)
        return _emit(report)

    if action.kind == RESOLVE:
        from .resolution import plan_resolution

        plan = plan_resolution(action.outcome, base=args.base, source_ref=args.source)
        results: list[dict] = []
        instructions: list[str] = []
        failed = False
        for step in plan.steps:
            if step.advisory:
                instructions.append(step.description)
                results.append({"kind": step.kind, "ran": False, "advisory": True})
                continue
            res = _run_resolution_step(step)
            results.append(res)
            if not res["ok"]:
                failed = True
                if step.destructive:
                    break
        report["executed"] = True
        report["resolution"] = {**plan.to_dict(), "results": results, "instructions": instructions}
        _emit(report)
        return 1 if failed else 0

    # WORK: nothing for the layer to execute -- the agent does the pass.
    report["executed"] = False
    report["note"] = "WORK is the agent's to perform; re-run drive with the next signal"
    return _emit(report)
