"""Tests for the loop-recipe registry and the ``recipes`` CLI."""

from __future__ import annotations

import json

import pytest

from agent_dispatch import recipes
from agent_dispatch.__main__ import (
    _cmd_recipes_kick,
    _cmd_recipes_list,
    _cmd_recipes_render,
    build_parser,
)


def _args(argv):
    return build_parser().parse_args(argv)


# -- registry ----------------------------------------------------------------


def test_registry_has_the_three_archetypes():
    names = {r.name for r in recipes.list_recipes()}
    assert names == {"reviewer", "conflict-resolution", "goal-driven"}


def test_get_recipe_unknown_raises():
    with pytest.raises(recipes.UnknownRecipe):
        recipes.get_recipe("nope")


def test_render_reviewer_fills_templates_and_default_base():
    r = recipes.render_recipe("reviewer", {"repo": "o/n", "pr": "42"})
    assert r.title == "review o/n#42 to resolution"
    assert "o/n#42" in r.goal
    # the optional base param defaulted
    assert "the default branch" in r.prompt
    # the recipe label is always present, plus the recipe's own labels
    assert "recipe:reviewer" in r.labels
    assert "kind:review" in r.labels
    # the shared safety clauses ride along in the charter
    assert "resolved state" in r.prompt
    assert r.resolution == "pull-request-merged-or-abandoned"


def test_render_explicit_base_overrides_default():
    r = recipes.render_recipe("reviewer", {"repo": "o/n", "pr": "42", "base": "release"})
    assert "release" in r.prompt
    assert "the default branch" not in r.prompt


def test_render_missing_required_param_raises_listing_them():
    with pytest.raises(recipes.RecipeError) as exc:
        recipes.render_recipe("reviewer", {"repo": "o/n"})
    assert "pr" in str(exc.value)


def test_render_ignores_unknown_params():
    r = recipes.render_recipe("goal-driven", {"goal": "document X", "bogus": "y"})
    assert r.title == "drive: document X"
    assert "bogus" not in r.prompt


# -- CLI parsing -------------------------------------------------------------


def test_cli_parses_recipes_subcommands():
    assert _args(["recipes", "list"]).func is _cmd_recipes_list
    assert _args(["recipes", "render", "reviewer"]).func is _cmd_recipes_render
    assert _args(["recipes", "kick", "reviewer"]).func is _cmd_recipes_kick


# -- CLI handlers ------------------------------------------------------------


def test_cmd_list_emits_all_recipes(capsys):
    rc = _cmd_recipes_list(_args(["recipes", "list"]))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert {r["name"] for r in out} == {"reviewer", "conflict-resolution", "goal-driven"}
    reviewer = next(r for r in out if r["name"] == "reviewer")
    assert reviewer["resolution"] == "pull-request-merged-or-abandoned"
    assert any(p["name"] == "pr" and p["required"] for p in reviewer["params"])


def test_cmd_render_emits_fields(capsys):
    args = _args(["recipes", "render", "reviewer", "--param", "repo=o/n", "--param", "pr=7"])
    rc = _cmd_recipes_render(args)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["title"] == "review o/n#7 to resolution"
    assert out["recipe"] == "reviewer"


def test_cmd_render_missing_param_errors(capsys):
    args = _args(["recipes", "render", "reviewer", "--param", "repo=o/n"])
    rc = _cmd_recipes_render(args)
    assert rc == 2
    assert "missing required parameter" in capsys.readouterr().err


def test_cmd_render_bad_param_format_errors(capsys):
    args = _args(["recipes", "render", "reviewer", "--param", "no-equals"])
    rc = _cmd_recipes_render(args)
    assert rc == 2
    assert "KEY=VALUE" in capsys.readouterr().err


def test_kick_dry_run_previews_without_creating(capsys):
    args = _args(
        ["recipes", "kick", "reviewer", "--param", "repo=o/n", "--param", "pr=7", "--dry-run"]
    )
    rc = _cmd_recipes_kick(args)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True
    assert out["title"] == "review o/n#7 to resolution"
    # a reserved-work dedup key is derived from recipe + params
    assert out["dedup_key"] == "recipe:reviewer:base=the default branch:pr=7:repo=o/n"


def test_kick_delegates_to_create_with_recipe_fields(monkeypatch):
    captured = {}

    def fake_create(ns):
        captured["ns"] = ns
        return 0

    monkeypatch.setattr("agent_dispatch.__main__._cmd_create", fake_create)
    args = _args(
        [
            "recipes", "kick", "conflict-resolution",
            "--param", "repo=o/n", "--param", "pr=9",
            "--repo", "o/n", "--spawn",
        ]
    )
    rc = _cmd_recipes_kick(args)
    assert rc == 0
    ns = captured["ns"]
    assert ns.title == "unstick o/n#9"
    assert ns.goal.startswith("Take o/n#9")
    assert "recipe:conflict-resolution" in ns.label
    assert ns.source == "recipe"
    assert ns.origin_ref == "conflict-resolution"
    assert ns.dedup_key == "recipe:conflict-resolution:base=the default branch:pr=9:repo=o/n"
    assert ns.repo == "o/n"
    assert ns.spawn is True
    # a recipe worker wants a full checkout -> the embody body by default
    assert ns.spawn_backend == "embody"


def test_kick_custom_dedup_key_wins(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "agent_dispatch.__main__._cmd_create",
        lambda ns: captured.setdefault("ns", ns) or 0,
    )
    args = _args(
        [
            "recipes", "kick", "reviewer",
            "--param", "repo=o/n", "--param", "pr=7",
            "--dedup-key", "custom-key",
        ]
    )
    _cmd_recipes_kick(args)
    assert captured["ns"].dedup_key == "custom-key"
