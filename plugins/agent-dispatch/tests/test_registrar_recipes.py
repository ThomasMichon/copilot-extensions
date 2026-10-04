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


# -- resolve_extends: chaining (extend any already-resolved declaration) ------

def test_resolve_extends_resolves_a_two_hop_chain(tmp_path, monkeypatch):
    """A declaration may extend a repo-local recipe that itself extends a
    `global:` recipe -- any already-resolved declaration is a valid base,
    not only a named recipe directly."""
    monkeypatch.setitem(
        GLOBAL_RECIPES, "chain-fixture", {"kind": "supervised-lane", "owner": "global-owner"}
    )
    recipe_dir = tmp_path / "recipes"
    recipe_dir.mkdir()
    (recipe_dir / "mid.json").write_text(
        json.dumps({"extends": "global:chain-fixture", "labels": ["mid-label"]}),
        encoding="utf-8",
    )
    data = {"extends": "./recipes/mid.json", "name": "leaf"}

    resolved = resolve_extends(data, base_dir=tmp_path)

    assert "extends" not in resolved
    assert resolved == {
        "kind": "supervised-lane",
        "owner": "global-owner",
        "labels": ["mid-label"],
        "name": "leaf",
    }


def test_resolve_extends_resolves_a_three_hop_chain_with_overrides_at_each_hop(
    tmp_path,
):
    """Each hop's own override fields deep-merge in order -- the closest
    (outermost) override wins on conflict, matching the single-hop
    contract applied repeatedly."""
    recipe_dir = tmp_path / "recipes"
    recipe_dir.mkdir()
    (recipe_dir / "root.json").write_text(
        json.dumps({"kind": "supervised-lane", "owner": "root-owner", "concurrency": 1}),
        encoding="utf-8",
    )
    (recipe_dir / "mid.json").write_text(
        json.dumps(
            {"extends": "./root.json", "owner": "mid-owner", "labels": ["mid"]}
        ),
        encoding="utf-8",
    )
    data = {"extends": "./recipes/mid.json", "owner": "leaf-owner"}

    resolved = resolve_extends(data, base_dir=tmp_path)

    assert resolved == {
        "kind": "supervised-lane",
        "owner": "leaf-owner",
        "concurrency": 1,
        "labels": ["mid"],
    }


def test_resolve_extends_chain_fills_placeholders_at_each_hop_in_order(tmp_path):
    """Placeholder substitution isn't only a single-hop concern: an
    intermediate hop and the leaf may each fill a *different* placeholder
    in the root template, and recursive ordering must still apply each
    hop's own scalar overrides before that hop's own merge runs."""
    recipe_dir = tmp_path / "recipes"
    recipe_dir.mkdir()
    (recipe_dir / "root.json").write_text(
        json.dumps(
            {"kind": "emitter", "spec": {"command": ["review", "{repo}", "{reviewer}"]}}
        ),
        encoding="utf-8",
    )
    # mid.json extends root.json and fills {reviewer} from its own override
    # -- {repo} is deliberately left for the leaf declaration to fill.
    (recipe_dir / "mid.json").write_text(
        json.dumps({"extends": "./root.json", "reviewer": "standing-bot"}),
        encoding="utf-8",
    )
    data = {"extends": "./recipes/mid.json", "repo": "owner/name"}

    resolved = resolve_extends(data, base_dir=tmp_path)

    assert resolved["spec"]["command"] == ["review", "owner/name", "standing-bot"]


def test_resolve_extends_chain_hop_resolves_nested_ref_against_its_own_directory(
    tmp_path,
):
    """A cross-repo base's own repo-relative `extends:` ref must resolve
    against **its own** directory, never the directory of whatever repo is
    extending it -- the literal bug a single shared `base_dir` across every
    hop would reproduce."""
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    # repo-b's own base.json -- only reachable relative to repo-b itself.
    (repo_b / "base.json").write_text(
        json.dumps({"kind": "supervised-lane", "owner": "repo-b-owner"}),
        encoding="utf-8",
    )
    # repo-b's mid.json, extended cross-repo from repo-a, names a
    # repo-relative ref that only resolves correctly against repo-b's own
    # directory.
    (repo_b / "mid.json").write_text(
        json.dumps({"extends": "./base.json", "labels": ["from-repo-b"]}),
        encoding="utf-8",
    )
    # A same-named decoy in repo-a: if the nested ref incorrectly resolved
    # against repo-a (the *extending* repo's base_dir) instead of repo-b
    # (mid.json's own directory), this decoy would be picked up instead,
    # and the test would observe the wrong owner.
    (repo_a / "base.json").write_text(
        json.dumps({"kind": "supervised-lane", "owner": "WRONG-repo-a-decoy"}),
        encoding="utf-8",
    )
    data = {"extends": str(repo_b / "mid.json")}

    resolved = resolve_extends(data, base_dir=repo_a)

    assert resolved == {
        "kind": "supervised-lane",
        "owner": "repo-b-owner",
        "labels": ["from-repo-b"],
    }


def test_resolve_extends_global_chain_hop_resolves_against_plugin_payload_root(
    tmp_path, monkeypatch
):
    """A `global:` recipe's own nested repo-relative `extends:` ref has no
    on-disk file backing the `global:` ref itself to derive a directory
    from -- it must resolve against the plugin's own attributed payload
    root (`COPILOT_PLUGIN_ROOT`), never this module's own Python package
    directory (several levels below the actual payload root)."""
    plugin_root = tmp_path / "attributed-plugin-root"
    plugin_root.mkdir()
    (plugin_root / "nested-base.json").write_text(
        json.dumps({"kind": "supervised-lane", "owner": "plugin-root-owner"}),
        encoding="utf-8",
    )
    monkeypatch.setitem(
        GLOBAL_RECIPES,
        "nested-ref-fixture",
        {"extends": "./nested-base.json", "labels": ["from-global"]},
    )
    monkeypatch.setenv("COPILOT_PLUGIN_ROOT", str(plugin_root))

    resolved = resolve_extends(
        {"extends": "global:nested-ref-fixture"}, base_dir=tmp_path / "unrelated"
    )

    assert resolved == {
        "kind": "supervised-lane",
        "owner": "plugin-root-owner",
        "labels": ["from-global"],
    }


def test_resolve_extends_rejects_a_cyclic_chain(tmp_path):
    """A → B → A must raise a clear `RegistrarError` naming the chain,
    never recurse forever / raise a bare `RecursionError`."""
    recipe_dir = tmp_path / "recipes"
    recipe_dir.mkdir()
    a_path = recipe_dir / "a.json"
    b_path = recipe_dir / "b.json"
    a_path.write_text(json.dumps({"extends": "./b.json"}), encoding="utf-8")
    b_path.write_text(json.dumps({"extends": "./a.json"}), encoding="utf-8")

    with pytest.raises(RegistrarError, match="cyclic reference chain") as excinfo:
        resolve_extends({"extends": "./recipes/a.json"}, base_dir=tmp_path)

    # Lock down the diagnostic contract: the error must name the actual
    # offending chain (a -> b -> a), not just a generic "cyclic" prefix --
    # an implementation that stopped naming the refs would still match the
    # loose "cyclic reference chain" pattern above but silently regress
    # this.
    message = str(excinfo.value)
    assert str(a_path.resolve()) in message
    assert str(b_path.resolve()) in message
    assert message.index(str(a_path.resolve())) < message.index(str(b_path.resolve()))
    assert message.count(str(a_path.resolve())) == 2


def test_resolve_extends_rejects_an_acyclic_chain_beyond_the_max_depth(tmp_path):
    """A long but never-repeating chain must still raise a clear
    `RegistrarError` once it exceeds the depth guard -- never exhaust the
    Python recursion limit with a bare `RecursionError` that would abort
    the entire plugin's declaration scan (`_classify_declaration` only
    catches `RegistrarError`)."""
    from agent_dispatch.registrar_recipes import _MAX_CHAIN_DEPTH

    recipe_dir = tmp_path / "recipes"
    recipe_dir.mkdir()
    chain_length = _MAX_CHAIN_DEPTH + 5
    for i in range(chain_length):
        next_ref = f"./hop-{i + 1}.json" if i + 1 < chain_length else None
        body = {"extends": next_ref} if next_ref else {"kind": "supervised-lane"}
        (recipe_dir / f"hop-{i}.json").write_text(json.dumps(body), encoding="utf-8")

    with pytest.raises(RegistrarError, match="exceeds the maximum depth"):
        resolve_extends({"extends": "./recipes/hop-0.json"}, base_dir=tmp_path)


def test_resolve_extends_canonicalizes_an_absolute_symlinked_ref(tmp_path):
    """An absolute ref that is itself a symlink must canonicalize to its
    real target before deriving the next hop's base directory -- otherwise
    a nested relative ref inside the symlinked document would incorrectly
    resolve against the symlink's own (wrong) directory instead of the
    real file's directory."""
    repo_real = tmp_path / "repo-real"
    repo_real.mkdir()
    (repo_real / "base.json").write_text(
        json.dumps({"kind": "supervised-lane", "owner": "real-repo-owner"}),
        encoding="utf-8",
    )
    (repo_real / "mid.json").write_text(
        json.dumps({"extends": "./base.json", "labels": ["from-real-repo"]}),
        encoding="utf-8",
    )
    symlink_dir = tmp_path / "symlinked-elsewhere"
    symlink_path = symlink_dir.parent / "mid-symlink.json"
    symlink_path.symlink_to(repo_real / "mid.json")

    resolved = resolve_extends(
        {"extends": str(symlink_path)}, base_dir=tmp_path / "unrelated"
    )

    assert resolved == {
        "kind": "supervised-lane",
        "owner": "real-repo-owner",
        "labels": ["from-real-repo"],
    }


def test_resolve_extends_absolutizes_a_base_emitter_cwd_against_its_own_directory(
    tmp_path,
):
    """A declaration extending an ordinary cross-repo emitter with a
    relative `spec.cwd` must run that emitter rooted in *its own*
    directory, not the extending declaration's directory --
    `read_declaration_file_set`'s own downstream path-rebase only rebases
    against the outermost (leaf) file, so this must already be absolute by
    the time this function returns."""
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    (repo_b / "base.json").write_text(
        json.dumps({"kind": "emitter", "spec": {"cwd": ".", "command": ["./run.sh"]}}),
        encoding="utf-8",
    )
    data = {"extends": str(repo_b / "base.json")}

    resolved = resolve_extends(data, base_dir=repo_a)

    assert resolved["spec"]["cwd"] == str(repo_b.resolve())


def test_resolve_extends_absolutizes_cwd_only_after_an_outer_placeholder_fills_it(
    tmp_path,
):
    """A base emitter's `cwd` may itself be a `{placeholder}` an outer
    override supplies (e.g. an absolute `workdir` parameter) -- this must
    be recognized as already-absolute once filled, never joined onto the
    base's own directory as if it were still relative text. Absolutizing
    before placeholder substitution would corrupt an absolute value into
    `<base-dir>/<absolute-value>`; absolutizing before resolving at all
    would instead leave a literal unresolved `{workdir}` joined onto the
    base directory."""
    recipe_dir = tmp_path / "recipes"
    recipe_dir.mkdir()
    (recipe_dir / "base.json").write_text(
        json.dumps({"kind": "emitter", "spec": {"cwd": "{workdir}", "command": ["x"]}}),
        encoding="utf-8",
    )
    absolute_workdir = str((tmp_path / "actual-workdir").resolve())
    data = {"extends": "./recipes/base.json", "workdir": absolute_workdir}

    resolved = resolve_extends(data, base_dir=tmp_path)

    assert resolved["spec"]["cwd"] == absolute_workdir


def test_resolve_extends_leaves_a_still_unresolved_cwd_placeholder_untouched(
    tmp_path,
):
    """A `cwd` placeholder this level's own overrides don't supply (left
    for a still-further outer hop) must be left completely alone -- never
    guessed at as a relative path segment and joined onto this hop's own
    directory, which would silently corrupt it instead of leaving it for
    the eventual outer fill."""
    recipe_dir = tmp_path / "recipes"
    recipe_dir.mkdir()
    (recipe_dir / "base.json").write_text(
        json.dumps({"kind": "emitter", "spec": {"cwd": "{workdir}", "command": ["x"]}}),
        encoding="utf-8",
    )
    data = {"extends": "./recipes/base.json"}

    resolved = resolve_extends(data, base_dir=tmp_path)

    assert resolved["spec"]["cwd"] == "{workdir}"


def test_resolve_extends_preserves_cwd_origin_across_an_unresolved_inheritance_hop(
    tmp_path,
):
    """A `cwd` placeholder may survive more than one hop: a root template
    sets `cwd: "{workdir}"`, an intermediate hop extends it without
    supplying `workdir`, and only the leaf finally does. The eventual
    absolutize must use the **root template's own directory** (where the
    field was written), never the intermediate hop's directory, even
    though the intermediate hop is the one whose own base_dir would
    otherwise be used if origin weren't tracked across the gap."""
    root_dir = tmp_path / "root"
    mid_dir = tmp_path / "mid"
    root_dir.mkdir()
    mid_dir.mkdir()
    (root_dir / "base.json").write_text(
        json.dumps({"kind": "emitter", "spec": {"cwd": "{workdir}", "command": ["x"]}}),
        encoding="utf-8",
    )
    # The intermediate hop extends root's base but supplies no `workdir` of
    # its own -- the placeholder passes through it unresolved.
    (mid_dir / "middle.json").write_text(
        json.dumps({"extends": str(root_dir / "base.json")}),
        encoding="utf-8",
    )
    # The leaf finally supplies workdir -- itself a *relative* value, so
    # the bug this test catches (resolving against mid_dir instead of
    # root_dir) would still produce a plausible-looking (but wrong) path.
    data = {"extends": str(mid_dir / "middle.json"), "workdir": "relative-workdir"}

    resolved = resolve_extends(data, base_dir=tmp_path)

    assert resolved["spec"]["cwd"] == str((root_dir / "relative-workdir").resolve())


def test_resolve_extends_an_override_cwd_resets_inherited_origin_tracking(tmp_path):
    """A hop's own override `spec.cwd` -- not inherited from its base --
    must win outright (ordinary deep-merge precedence) and must not be
    second-guessed by a still-pending inherited origin from deeper in the
    chain; that origin tracking only ever applies to the *inherited*
    value, never to a hop's own explicit replacement."""
    recipe_dir = tmp_path / "recipes"
    recipe_dir.mkdir()
    (recipe_dir / "base.json").write_text(
        json.dumps({"kind": "emitter", "spec": {"cwd": "{workdir}", "command": ["x"]}}),
        encoding="utf-8",
    )
    own_dir = tmp_path / "own-cwd-dir"
    own_dir.mkdir()
    data = {
        "extends": "./recipes/base.json",
        "spec": {"cwd": str(own_dir)},
    }

    resolved = resolve_extends(data, base_dir=tmp_path)

    assert resolved["spec"]["cwd"] == str(own_dir)


def test_resolve_extends_converts_a_resolve_runtime_error_to_a_registrar_error(
    tmp_path, monkeypatch
):
    """`Path.resolve()` raises `RuntimeError` for a symlink loop on some
    Python versions -- this must surface as a `RegistrarError` (what
    `_classify_declaration` actually catches), never an uncaught
    `RuntimeError` that would abort the entire plugin's declaration scan.
    Mocked rather than relying on real symlink-loop detection, which is
    slow and platform/filesystem-dependent to trigger deterministically."""
    original_resolve = Path.resolve

    def _raise_runtime_error(self, *args, **kwargs):
        if self.name == "a.json":
            raise RuntimeError("Symlink loop from 'a.json'")
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", _raise_runtime_error)

    with pytest.raises(RegistrarError, match="could not be resolved to a path"):
        resolve_extends({"extends": str(tmp_path / "a.json")}, base_dir=tmp_path)


def test_resolve_extends_converts_a_resolve_os_error_to_an_indeterminate_error(
    tmp_path, monkeypatch
):
    """`Path.resolve()` raises `OSError` for a symlink loop on newer Python
    versions -- this is a transient-condition classification (indeterminate,
    like `_load_recipe_document`'s own `OSError` handling), not a hard
    invalidation of the whole declaration."""
    from agent_dispatch.registrar_discovery import RegistrarIndeterminateError

    original_resolve = Path.resolve

    def _raise_os_error(self, *args, **kwargs):
        if self.name == "a.json":
            raise OSError("Too many levels of symbolic links")
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", _raise_os_error)

    with pytest.raises(RegistrarIndeterminateError, match="could not resolve recipe ref"):
        resolve_extends({"extends": str(tmp_path / "a.json")}, base_dir=tmp_path)


def test_resolve_extends_can_extend_an_arbitrary_direct_declaration(tmp_path):
    """The base need not be authored specifically as a "recipe template" --
    any valid declaration document is a valid base, including one that was
    first written as an ordinary direct (non-`extends:`) declaration."""
    recipe_dir = tmp_path / "recipes"
    recipe_dir.mkdir()
    # A plain, ordinary direct declaration -- no `extends:`, no placeholders,
    # written exactly as a real standalone profile would be.
    (recipe_dir / "ordinary.json").write_text(
        json.dumps(
            {
                "kind": "supervised-lane",
                "name": "ordinary-lane",
                "labels": ["ordinary"],
                "concurrency": 2,
            }
        ),
        encoding="utf-8",
    )
    data = {"extends": "./recipes/ordinary.json", "name": "specialized-lane"}

    resolved = resolve_extends(data, base_dir=tmp_path)

    assert resolved == {
        "kind": "supervised-lane",
        "name": "specialized-lane",
        "labels": ["ordinary"],
        "concurrency": 2,
    }


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


def test_global_backlog_triager_resolves_to_repository_issue_loop_with_verification(
    tmp_path,
):
    import json as _json

    from agent_dispatch.registrar_discovery import read_declaration_file_set

    path = tmp_path / "triager.json"
    path.write_text(
        _json.dumps(
            {
                "extends": "global:backlog-triager",
                "name": "triage-backlog",
                "repo": "example/project",
                "source": "triage-backlog",
                "cadence_seconds": 3600,
                "task_label": "backlog-triage",
                "forge": {"provider": "github", "producer_login": "triage-bot"},
                "reservation": {"label": "triage-reserved"},
                "pool": {
                    "max_active_processes": 1,
                    "body": {"agent": "triage-worker"},
                },
            }
        ),
        encoding="utf-8",
    )

    declarations = read_declaration_file_set(path)

    workers = next(d for d in declarations if d.name == "triage-backlog-workers")
    assert workers.body.type == "headless"
    assert workers.body.agent == "triage-worker"
    source = next(d for d in declarations if d.name == "triage-backlog-source")
    spec = source.spec["repository_issue_loop"]
    assert spec["forge"]["provider"] == "github"
    assert spec["worker_identity"] == "backlog-triager"
    assert spec["require_verification"] is True
    assert spec["evaluator_ref"] == "backlog-triager"


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


def test_backlog_triager_builtin_identity_resolves():
    from agent_dispatch.worker_identities import load_worker_identity

    identity = load_worker_identity("backlog-triager")

    assert identity.name == "backlog-triager"
    assert "legitimate bug" in identity.rules
    assert "efforts/active/<slug>/README.md" in identity.rules
