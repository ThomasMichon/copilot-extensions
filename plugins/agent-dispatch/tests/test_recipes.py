"""Tests for the loop-recipe registry and the ``recipes`` CLI."""

from __future__ import annotations

import json
from types import SimpleNamespace

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
    assert "landing:self" in r.labels
    assert "resolution:reviewer-self" in r.labels
    # the shared safety clauses ride along in the charter
    assert "resolved state" in r.prompt
    assert r.resolution == "pull-request-merged-or-abandoned"


def test_render_explicit_base_overrides_default():
    r = recipes.render_recipe("reviewer", {"repo": "o/n", "pr": "42", "base": "release"})
    assert "release" in r.prompt
    assert "the default branch" not in r.prompt


def test_render_reviewer_author_landing_has_distinct_contract():
    r = recipes.render_recipe(
        "reviewer", {"repo": "o/n", "pr": "42", "land": "author"}
    )
    assert "landing:author" in r.labels
    assert "resolution:reviewer-author" in r.labels
    assert r.resolution == "review-delivered-then-author-resolved-or-expired"
    assert "Never merge on the author's behalf" in r.prompt


def test_render_reviewer_rejects_unknown_landing_model():
    with pytest.raises(recipes.RecipeError, match="self.*author"):
        recipes.render_recipe(
            "reviewer", {"repo": "o/n", "pr": "42", "land": "bot"}
        )


def test_charter_points_at_the_concrete_hibernate_and_resolve_verbs():
    # a recipe worker is told to use the real `run` (hibernate) and `resolve`
    # verbs, not just the abstract "suspend / drive to resolution" idea
    for name in ("reviewer", "conflict-resolution", "goal-driven"):
        r = recipes.get_recipe(name)
        assert "agent-dispatch run --detach" in r.charter_template
        assert "agent-dispatch resolve" in r.charter_template


def test_conflict_resolution_charter_names_producer_origin_and_force_push():
    # The conflict-resolution recipe IS the "PR reconciler": an automated
    # producer opens a PR, it conflicts, an agent is dispatched to solve it by
    # rebasing and force-pushing the resolution back over the SAME PR (never a
    # second PR). Lock that intent into the charter so it can't quietly drift.
    r = recipes.get_recipe("conflict-resolution")
    charter = r.charter_template.lower()
    assert "automated producer" in charter
    assert "force-push" in charter
    assert "same pr" in charter
    assert "never open a second pr" in charter


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
    assert reviewer["resolution"] == "review-delivered-or-pull-request-resolved"
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
    assert out["dedup_key"] == "recipe:reviewer:target=github.com/o/n#7"


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
    assert ns.evaluator_ref is None
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


def test_kick_merges_extra_labels_after_recipe_labels(monkeypatch):
    """`--label` stamps extra labels (e.g. to route the task onto a supervisor
    pool), merged after -- and de-duplicated with -- the recipe's own labels."""
    captured = {}
    monkeypatch.setattr(
        "agent_dispatch.__main__._cmd_create",
        lambda ns: captured.setdefault("ns", ns) or 0,
    )
    args = _args(
        [
            "recipes", "kick", "goal-driven",
            "--param", "goal=Fix the widget",
            "--label", "general", "--label", "kind:goal",  # second is already a recipe label
        ]
    )
    _cmd_recipes_kick(args)
    label = captured["ns"].label
    # recipe's own labels lead, the new pool label is appended, no duplicates
    assert label[0] == "recipe:goal-driven"
    assert "kind:goal" in label
    assert "general" in label
    assert label.count("kind:goal") == 1
    assert label.count("general") == 1


def test_kick_without_labels_is_recipe_labels_only(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "agent_dispatch.__main__._cmd_create",
        lambda ns: captured.setdefault("ns", ns) or 0,
    )
    _cmd_recipes_kick(_args(["recipes", "kick", "goal-driven", "--param", "goal=x"]))
    assert captured["ns"].label == ["recipe:goal-driven", "kind:goal"]


# -- MCP tools ---------------------------------------------------------------


class _FakeClient:
    """Context-manager stand-in for DispatchClient capturing create() calls."""

    def __init__(self, sink):
        self._sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def create(self, title, **kwargs):
        self._sink.update(title=title, **kwargs)
        return {"id": "task-1", "title": title, "status": "queued"}


def _tools(sink):
    from agent_dispatch.mcp_server import DispatchTools

    return DispatchTools(
        client_factory=lambda: _FakeClient(sink),
        repo_resolver=lambda: "https://example.com/o/n.git",
    )


def test_mcp_recipe_list_returns_archetypes():
    out = _tools({}).recipe_list()
    assert {r["name"] for r in out} == {"reviewer", "conflict-resolution", "goal-driven"}


def test_mcp_recipe_render_is_pure():
    out = _tools({}).recipe_render("reviewer", {"repo": "o/n", "pr": "5"})
    assert out["title"] == "review o/n#5 to resolution"


def test_mcp_recipe_kick_enqueues_with_recipe_fields():
    sink: dict = {}
    result = _tools(sink).recipe_kick("reviewer", params={"repo": "o/n", "pr": "5"})
    assert result["status"] == "queued"
    assert sink["title"] == "review o/n#5 to resolution"
    assert sink["repo"] == "https://example.com/o/n.git"  # resolved lane
    assert sink["source"] == "recipe"
    assert sink["origin_ref"] == "reviewer"
    assert "recipe:reviewer" in sink["labels"]
    assert sink["dedup_key"] == "recipe:reviewer:target=github.com/o/n#5"
    assert sink["goal"] == "Review pull request o/n#5 under the self landing model."


def test_mcp_recipe_kick_missing_param_raises():
    with pytest.raises(recipes.RecipeError):
        _tools({}).recipe_kick("reviewer", params={"repo": "o/n"})


def test_mcp_recipe_kick_merges_extra_labels():
    sink: dict = {}
    _tools(sink).recipe_kick(
        "goal-driven", params={"goal": "Fix the widget"}, labels=["general", "kind:goal"]
    )
    labels = sink["labels"]
    assert labels[0] == "recipe:goal-driven"
    assert "general" in labels
    assert labels.count("kind:goal") == 1  # de-duplicated with the recipe's own


def test_cli_and_mcp_derive_the_same_dedup_key():
    from agent_dispatch.__main__ import _recipe_dedup_key

    rendered = recipes.render_recipe("goal-driven", {"goal": "document X"})
    assert _recipe_dedup_key(rendered) == recipes.dedup_key_for(rendered)


def test_local_mcp_emitter_side_load_routes_to_remote_owner(monkeypatch):
    from agent_dispatch import remote_dispatch
    from agent_dispatch.mcp_server import DispatchTools

    calls = []

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def get_registration(self, _rid):
            return {
                "id": "emitter-reviews",
                "kind": "emitter",
                "machine": "host-b",
                "env": "staging",
            }

    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "host-a")
    monkeypatch.setattr(
        remote_dispatch,
        "browse_remote",
        lambda machine, argv, timeout=None: (
            calls.append((machine, argv, timeout))
            or SimpleNamespace(
                returncode=0,
                stdout='{"registration_id":"emitter-reviews","created":[]}',
                stderr="",
            )
        ),
    )
    tools = DispatchTools(client_factory=Client)
    out = tools.emitter_side_load("emitter-reviews", "o/n#7")
    assert out["registration_id"] == "emitter-reviews"
    assert calls[0][0] == "host-b"
    assert calls[0][1][-2:] == ["--env", "staging"]


def test_reviewer_dedup_ignores_config_drift():
    first = recipes.render_recipe(
        "reviewer", {"repo": "o/n", "pr": "5", "land": "self"}
    )
    changed = recipes.render_recipe(
        "reviewer",
        {"repo": "o/n", "pr": "5", "land": "author", "base": "release"},
    )
    assert recipes.dedup_key_for(first) == recipes.dedup_key_for(changed)


def test_reviewer_dedup_canonicalizes_equivalent_target_references():
    short = recipes.render_recipe("reviewer", {"repo": "o/n", "pr": "#5"})
    remote = recipes.render_recipe(
        "reviewer", {"repo": "https://github.com/o/n.git", "pr": "5"}
    )
    assert recipes.dedup_key_for(short) == recipes.dedup_key_for(remote)


def test_reviewer_dedup_preserves_forge_identity():
    github = recipes.render_recipe(
        "reviewer", {"repo": "https://github.com/o/n.git", "pr": "5"}
    )
    gitlab = recipes.render_recipe(
        "reviewer", {"repo": "https://gitlab.com/o/n.git", "pr": "5"}
    )
    assert recipes.dedup_key_for(github) != recipes.dedup_key_for(gitlab)
