"""Tests for the registrar `extends:` resolution mechanism (recipe refs +
deep-merge) -- see `registrar_recipes.py` and
`efforts/active/agent-dispatch-recipe-library/phase-3-extends-registrar.md`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_dispatch.registrar import RegistrarError
from agent_dispatch.registrar_recipes import (
    GLOBAL_RECIPES,
    deep_merge,
    resolve_extends,
    resolve_recipe_ref,
    substitute_placeholders,
)


# -- deep_merge ---------------------------------------------------------------

def test_deep_merge_override_wins_on_scalar_conflict():
    assert deep_merge({"a": 1, "b": 2}, {"b": 3}) == {"a": 1, "b": 3}


def test_deep_merge_recurses_into_nested_mappings():
    base = {"forge": {"provider": "github", "producer_login": "bot"}}
    override = {"forge": {"producer_login": "other-bot"}}
    assert deep_merge(base, override) == {
        "forge": {"provider": "github", "producer_login": "other-bot"}
    }


def test_deep_merge_replaces_lists_wholesale_not_concatenated():
    base = {"include_labels": ["ready"]}
    override = {"include_labels": ["ready", "priority"]}
    assert deep_merge(base, override) == {"include_labels": ["ready", "priority"]}


def test_deep_merge_passes_through_base_only_keys():
    assert deep_merge({"a": 1}, {}) == {"a": 1}


def test_deep_merge_a_mapping_overriding_a_scalar_replaces_wholesale():
    assert deep_merge({"a": 1}, {"a": {"nested": True}}) == {"a": {"nested": True}}


# -- resolve_recipe_ref: global: ----------------------------------------------

def test_resolve_recipe_ref_global_unknown_name_fails_loud(tmp_path):
    with pytest.raises(RegistrarError, match="unknown global recipe 'nope'"):
        resolve_recipe_ref("global:nope", base_dir=tmp_path)


def test_resolve_recipe_ref_global_resolves_a_registered_recipe(tmp_path, monkeypatch):
    monkeypatch.setitem(GLOBAL_RECIPES, "fixture", {"kind": "supervised-lane"})
    assert resolve_recipe_ref("global:fixture", base_dir=tmp_path) == {
        "kind": "supervised-lane"
    }


# -- resolve_recipe_ref: file path (repo-local / cross-repo) ------------------

def test_resolve_recipe_ref_relative_path_resolves_against_base_dir(tmp_path):
    recipe_dir = tmp_path / "recipes"
    recipe_dir.mkdir()
    (recipe_dir / "my-loop.json").write_text(
        json.dumps({"kind": "supervised-lane", "labels": ["x"]}), encoding="utf-8"
    )
    result = resolve_recipe_ref("./recipes/my-loop.json", base_dir=tmp_path)
    assert result == {"kind": "supervised-lane", "labels": ["x"]}


def test_resolve_recipe_ref_absolute_path_ignores_base_dir(tmp_path):
    other_dir = tmp_path / "elsewhere"
    other_dir.mkdir()
    recipe = other_dir / "cross-repo.json"
    recipe.write_text(json.dumps({"kind": "supervised-lane"}), encoding="utf-8")
    unrelated_base = tmp_path / "unrelated"
    unrelated_base.mkdir()
    assert resolve_recipe_ref(str(recipe), base_dir=unrelated_base) == {
        "kind": "supervised-lane"
    }


def test_resolve_recipe_ref_missing_file_fails_loud(tmp_path):
    with pytest.raises(RegistrarError, match="could not read recipe file"):
        resolve_recipe_ref("./recipes/missing.json", base_dir=tmp_path)


def test_resolve_recipe_ref_invalid_encoding_raises_registrar_error(tmp_path):
    bad = tmp_path / "bad-encoding.json"
    bad.write_bytes(b"\xff\xfe\x00not valid utf-8")
    with pytest.raises(RegistrarError, match="invalid encoding"):
        resolve_recipe_ref(str(bad), base_dir=tmp_path)


def test_resolve_recipe_ref_invalid_path_characters_raise_registrar_error(tmp_path):
    """An embedded NUL (or any other character the OS path layer rejects)
    raises a bare `ValueError` from `Path()`/`.resolve()`/`.read_text()` --
    that must never escape as an unclassified exception."""
    with pytest.raises(RegistrarError, match="could not be resolved to a path"):
        resolve_recipe_ref("./bad\x00name.json", base_dir=tmp_path)


def test_resolve_recipe_ref_rejects_empty_ref(tmp_path):
    with pytest.raises(RegistrarError, match="non-empty string ref"):
        resolve_recipe_ref("", base_dir=tmp_path)


def test_resolve_recipe_ref_rejects_a_null_ref(tmp_path):
    with pytest.raises(RegistrarError, match="non-empty string ref"):
        resolve_recipe_ref(None, base_dir=tmp_path)


def test_resolve_recipe_ref_rejects_an_unsupported_suffix(tmp_path):
    (tmp_path / "recipe.txt").write_text("not a declaration document", encoding="utf-8")
    with pytest.raises(RegistrarError, match="unrecognized suffix"):
        resolve_recipe_ref("./recipe.txt", base_dir=tmp_path)


# -- resolve_extends ------------------------------------------------------------

def test_resolve_extends_passes_through_data_with_no_extends_key():
    data = {"name": "general", "labels": ["general"]}
    assert resolve_extends(data, base_dir=Path(".")) == data


def test_resolve_extends_rejects_a_present_but_null_extends_key(tmp_path):
    """A present `extends: null` must not be conflated with an absent key --
    that would silently bypass validation and surface a misleading
    unrelated error (e.g. 'unknown key: extends') further down the
    pipeline instead of this clear one."""
    with pytest.raises(RegistrarError, match="non-empty string ref"):
        resolve_extends({"extends": None, "name": "x"}, base_dir=tmp_path)


def test_resolve_extends_merges_overrides_over_the_resolved_template(tmp_path):
    recipe_dir = tmp_path / "recipes"
    recipe_dir.mkdir()
    (recipe_dir / "base.json").write_text(
        json.dumps(
            {
                "kind": "supervised-lane",
                "labels": ["template-label"],
                "owner": "template-owner",
            }
        ),
        encoding="utf-8",
    )
    data = {
        "extends": "./recipes/base.json",
        "name": "concrete-lane",
        "owner": "real-owner",
    }

    resolved = resolve_extends(data, base_dir=tmp_path)

    assert "extends" not in resolved
    assert resolved == {
        "kind": "supervised-lane",
        "labels": ["template-label"],
        "owner": "real-owner",
        "name": "concrete-lane",
    }


def test_resolve_extends_substitutes_placeholders_from_scalar_overrides(tmp_path):
    recipe_dir = tmp_path / "recipes"
    recipe_dir.mkdir()
    (recipe_dir / "base.json").write_text(
        json.dumps(
            {
                "kind": "emitter",
                "spec": {"command": ["review", "{repo}"]},
            }
        ),
        encoding="utf-8",
    )
    data = {"extends": "./recipes/base.json", "repo": "owner/name"}

    resolved = resolve_extends(data, base_dir=tmp_path)

    assert resolved["spec"]["command"] == ["review", "owner/name"]


def test_resolve_extends_params_block_fills_placeholders_without_leaking_into_output(
    tmp_path,
):
    """`params:` supplies substitution-only values for a template field that
    isn't itself a valid top-level key of the resolved declaration (e.g. a
    provider login interpolated into a nested command string) -- it must
    never survive into the resolved output, unlike an ordinary override
    key such as `repo` above."""
    recipe_dir = tmp_path / "recipes"
    recipe_dir.mkdir()
    (recipe_dir / "base.json").write_text(
        json.dumps(
            {
                "kind": "emitter",
                "spec": {"command": ["review", "--login", "{producer_login}"]},
            }
        ),
        encoding="utf-8",
    )
    data = {
        "extends": "./recipes/base.json",
        "name": "concrete-lane",
        "params": {"producer_login": "issue-bot"},
    }

    resolved = resolve_extends(data, base_dir=tmp_path)

    assert resolved["spec"]["command"] == ["review", "--login", "issue-bot"]
    assert "params" not in resolved
    assert "producer_login" not in resolved


def test_resolve_extends_params_block_wins_substitution_precedence_over_override(
    tmp_path,
):
    """When the same key is supplied both as an ordinary override and
    inside `params:`, the `params:` value wins for substitution purposes --
    it exists precisely to let an author adjust a template's filled value
    without changing what the resolved declaration itself contains."""
    recipe_dir = tmp_path / "recipes"
    recipe_dir.mkdir()
    (recipe_dir / "base.json").write_text(
        json.dumps({"kind": "emitter", "spec": {"command": ["{name}"]}}),
        encoding="utf-8",
    )
    data = {
        "extends": "./recipes/base.json",
        "name": "override-value",
        "params": {"name": "params-value"},
    }

    resolved = resolve_extends(data, base_dir=tmp_path)

    assert resolved["spec"]["command"] == ["params-value"]
    # the ordinary override key still ends up in the resolved output, same
    # as always -- only the substitution *source* preferred `params:`.
    assert resolved["name"] == "override-value"


def test_resolve_extends_rejects_a_non_mapping_params_block(tmp_path):
    (tmp_path / "base.json").write_text(json.dumps({"kind": "emitter"}), encoding="utf-8")
    with pytest.raises(RegistrarError, match="'params' must be a mapping"):
        resolve_extends(
            {"extends": "./base.json", "params": ["not", "a", "mapping"]},
            base_dir=tmp_path,
        )


def test_resolve_extends_leaves_an_unresolved_placeholder_intact(tmp_path):
    recipe_dir = tmp_path / "recipes"
    recipe_dir.mkdir()
    (recipe_dir / "base.json").write_text(
        json.dumps({"kind": "emitter", "spec": {"command": ["{unprovided}"]}}),
        encoding="utf-8",
    )
    resolved = resolve_extends(
        {"extends": "./recipes/base.json"}, base_dir=tmp_path
    )
    assert resolved["spec"]["command"] == ["{unprovided}"]


def test_resolve_extends_leaves_json_like_prose_braces_completely_untouched(tmp_path):
    """A template's prose (e.g. worker guidance) may legitimately contain
    literal braces documenting an expected output shape -- substitution must
    never raise on, or mangle, content like this (a plain `str.Formatter`
    `.format_map()` call would raise `ValueError` on the embedded quotes/
    colon here)."""
    recipe_dir = tmp_path / "recipes"
    recipe_dir.mkdir()
    guidance = 'Return {"decision": "emit"} when done, and {literal} is untouched too.'
    (recipe_dir / "base.json").write_text(
        json.dumps({"kind": "emitter", "spec": {"worker_guidance": guidance}}),
        encoding="utf-8",
    )
    resolved = resolve_extends(
        {"extends": "./recipes/base.json"}, base_dir=tmp_path
    )
    assert resolved["spec"]["worker_guidance"] == guidance


def test_resolve_extends_leaves_doubled_braces_completely_untouched(tmp_path):
    """`{{name}}` is a literal-brace escaping convention (mirroring
    `str.format`'s own `{{`/`}}` escape), not a placeholder -- it must never
    be substituted into, even when `name` is a provided param."""
    recipe_dir = tmp_path / "recipes"
    recipe_dir.mkdir()
    (recipe_dir / "base.json").write_text(
        json.dumps({"kind": "emitter", "spec": {"command": ["{{name}}"]}}),
        encoding="utf-8",
    )
    resolved = resolve_extends(
        {"extends": "./recipes/base.json", "name": "concrete"}, base_dir=tmp_path
    )
    assert resolved["spec"]["command"] == ["{{name}}"]


def test_resolve_extends_only_uses_scalar_overrides_for_substitution(tmp_path):
    """A nested-mapping/list override value can't sensibly fill a string
    placeholder -- it must not be stringified into one either."""
    recipe_dir = tmp_path / "recipes"
    recipe_dir.mkdir()
    (recipe_dir / "base.json").write_text(
        json.dumps({"kind": "emitter", "spec": {"command": ["{forge}"]}}),
        encoding="utf-8",
    )
    resolved = resolve_extends(
        {"extends": "./recipes/base.json", "forge": {"provider": "github"}},
        base_dir=tmp_path,
    )
    assert resolved["spec"]["command"] == ["{forge}"]


def test_substitute_placeholders_rejects_a_self_referential_mapping():
    """A YAML document's self-referential alias (a mapping that -- directly
    or transitively -- contains itself) must fail loud with a
    `RegistrarError`, never a bare `RecursionError` that could abort the
    whole registrar scan over one malformed recipe."""
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic
    with pytest.raises(RegistrarError, match="cyclic reference"):
        substitute_placeholders(cyclic, {})


def test_substitute_placeholders_rejects_a_self_referential_list():
    cyclic: list[object] = []
    cyclic.append(cyclic)
    with pytest.raises(RegistrarError, match="cyclic reference"):
        substitute_placeholders(cyclic, {})


def test_substitute_placeholders_allows_a_non_cyclic_shared_reference():
    """A plain (non-cyclic) shared sub-object reachable from two different
    sibling branches -- an ordinary YAML anchor reused twice, never from
    itself -- must still resolve normally, not be mistaken for a cycle."""
    shared = {"command": ["{repo}"]}
    data = {"a": shared, "b": shared}
    resolved = substitute_placeholders(data, {"repo": "owner/name"})
    assert resolved == {
        "a": {"command": ["owner/name"]},
        "b": {"command": ["owner/name"]},
    }


def test_substitute_placeholders_rejects_a_cycle_that_passes_through_a_tuple():
    """A cycle routed through a tuple (e.g. a mapping containing a tuple
    that contains that same mapping) must still be detected -- the tuple
    branch must preserve the ancestor chain, not reset it, or the cycle
    would recurse past the tuple undetected into `RecursionError`."""
    cyclic: dict[str, object] = {}
    cyclic["via_tuple"] = (cyclic,)
    with pytest.raises(RegistrarError, match="cyclic reference"):
        substitute_placeholders(cyclic, {})


def test_resolve_extends_rejects_a_non_mapping_recipe_document(tmp_path, monkeypatch):
    # A file-path ref already can't produce a non-mapping (the decoder itself
    # enforces a mapping document); this guards the `global:` path, where a
    # misregistered recipe could bypass that.
    monkeypatch.setitem(GLOBAL_RECIPES, "bad", [1, 2, 3])
    with pytest.raises(RegistrarError, match="resolved to a non-mapping document"):
        resolve_extends({"extends": "global:bad"}, base_dir=tmp_path)


# -- The four shipped global recipes (Sub-PR 2) -------------------------------
#
# Each of the four named archetypes (ThomasMichon/copilot-extensions#4691
# Phase 3's own Sub-PR 2 description) resolves end-to-end through
# `read_declaration_file_set` -- the same chokepoint a hand-written direct
# `kind:` declaration goes through -- to prove the "no behavior change to the
# existing direct-declaration path" guarantee at the engine level, not just
# at the raw-dict-merge level `resolve_extends` alone already covers above.


def test_global_repository_issue_loop_equivalent_to_direct_kind(tmp_path):
    import json as _json

    from agent_dispatch.registrar_discovery import read_declaration_file_set

    override = {
        "name": "backlog",
        "repo": "example/project",
        "source": "repository-backlog",
        "cadence_seconds": 3600,
        "task_label": "repository-issue-work",
        "forge": {"provider": "github", "producer_login": "issue-bot"},
        "reservation": {"label": "agent-reserved"},
        "pool": {
            "max_active_processes": 1,
            "body": {"agent": "issue-worker"},
        },
    }

    direct_path = tmp_path / "direct.json"
    direct_path.write_text(
        _json.dumps(
            {
                **override,
                "kind": "repository-issue-loop",
                "exclude_labels": [
                    "bootstrap", "wontfix", "invalid", "duplicate", "question"
                ],
                "pool": {**override["pool"], "body": {
                    "type": "headless", **override["pool"]["body"],
                }},
            }
        ),
        encoding="utf-8",
    )
    extends_path = tmp_path / "extends.json"
    extends_path.write_text(
        _json.dumps({**override, "extends": "global:repository-issue-loop"}),
        encoding="utf-8",
    )

    direct = read_declaration_file_set(direct_path)
    extended = read_declaration_file_set(extends_path)

    assert extended == direct


def test_global_goal_driven_resolves_to_repository_issue_loop_with_identity(tmp_path):
    import json as _json

    from agent_dispatch.registrar_discovery import read_declaration_file_set

    path = tmp_path / "goal.json"
    path.write_text(
        _json.dumps(
            {
                "extends": "global:goal-driven",
                "name": "goal-backlog",
                "repo": "example/project",
                "source": "goal-backlog",
                "cadence_seconds": 3600,
                "task_label": "goal-work",
                "forge": {"provider": "github", "producer_login": "goal-bot"},
                "reservation": {"label": "goal-reserved"},
                "pool": {
                    "max_active_processes": 1,
                    "body": {"agent": "goal-worker"},
                },
            }
        ),
        encoding="utf-8",
    )

    declarations = read_declaration_file_set(path)

    workers = next(d for d in declarations if d.name == "goal-backlog-workers")
    assert workers.body.type == "headless"
    assert workers.body.agent == "goal-worker"
    # worker_identity: goal-driven threads through repository_issue_loops'
    # own worker-identity resolution into the emitter spec it stamps onto
    # each created task -- not directly onto the pool's own `body`.
    source = next(d for d in declarations if d.name == "goal-backlog-source")
    assert "goal-driven" in str(source.spec)


def test_global_reviewer_resolves_to_reviewer_loop_with_standing_charter(tmp_path):
    import json as _json

    from agent_dispatch.registrar_discovery import read_declaration_file_set

    path = tmp_path / "reviewer.json"
    path.write_text(
        _json.dumps(
            {
                "extends": "global:reviewer",
                "name": "my-reviewer",
                "repo": "github.com/example/project",
                "task_label": "external-review",
                "emitter": {
                    "command": ["python", "discover.py"],
                    "interval_seconds": 60,
                },
                "evaluator": {"evaluator_spec": {"rules": []}, "interval": 30},
                "pool": {"max_active_processes": 2},
            }
        ),
        encoding="utf-8",
    )

    declarations = read_declaration_file_set(path)

    workers = next(d for d in declarations if d.name == "my-reviewer-workers")
    assert workers.body.type == "headless"
    assert "standing reviewer" in workers.body.charter
    assert "land=self" in workers.body.charter


def test_global_conflict_resolution_resolves_to_reviewer_loop_with_standing_charter(
    tmp_path,
):
    import json as _json

    from agent_dispatch.registrar_discovery import read_declaration_file_set

    path = tmp_path / "conflict.json"
    path.write_text(
        _json.dumps(
            {
                "extends": "global:conflict-resolution",
                "name": "unstick",
                "repo": "github.com/example/project",
                "task_label": "conflict-resolution",
                "emitter": {
                    "command": ["python", "discover.py"],
                    "interval_seconds": 60,
                },
                "evaluator": {"evaluator_spec": {"rules": []}, "interval": 30},
                "pool": {"max_active_processes": 1},
            }
        ),
        encoding="utf-8",
    )

    declarations = read_declaration_file_set(path)

    workers = next(d for d in declarations if d.name == "unstick-workers")
    assert workers.body.type == "headless"
    assert "force-push" in workers.body.charter


def test_global_reviewer_declaration_can_override_the_default_charter(tmp_path):
    """A consumer can still supply its own `pool.body.charter`, replacing
    the template's default outright (deep_merge's ordinary scalar-override
    behavior) -- the shipped charter is a default, not a forced value."""
    import json as _json

    from agent_dispatch.registrar_discovery import read_declaration_file_set

    path = tmp_path / "reviewer.json"
    path.write_text(
        _json.dumps(
            {
                "extends": "global:reviewer",
                "name": "my-reviewer",
                "repo": "github.com/example/project",
                "task_label": "external-review",
                "emitter": {
                    "command": ["python", "discover.py"],
                    "interval_seconds": 60,
                },
                "evaluator": {"evaluator_spec": {"rules": []}, "interval": 30},
                "pool": {
                    "max_active_processes": 2,
                    "body": {"charter": "a completely custom charter"},
                },
            }
        ),
        encoding="utf-8",
    )

    declarations = read_declaration_file_set(path)

    workers = next(d for d in declarations if d.name == "my-reviewer-workers")
    assert workers.body.charter == "a completely custom charter"


def test_goal_driven_builtin_identity_resolves():
    from agent_dispatch.worker_identities import load_worker_identity

    identity = load_worker_identity("goal-driven")

    assert identity.name == "goal-driven"
    assert "drive it to completion" in identity.rules
