"""Tests for codespace_plugins: resolving CodeSpace-scoped plugins from the
harness's installed plugin arrangement."""

from __future__ import annotations

import json
from pathlib import Path

from agent_codespaces.codespace_plugins import (
    CodespacePluginSpec,
    enabled_plugin_names,
    is_harness_plugin,
    iter_installed_manifests,
    plugin_names_from_enabled,
    repo_matches,
    resolve_codespace_plugins,
)


# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------

def _install_plugin(
    copilot_home: Path,
    marketplace: str,
    name: str,
    *,
    codespace_plugins: list[dict] | None = None,
    extra: dict | None = None,
) -> None:
    """Write a fake installed plugin payload under <home>/installed-plugins."""
    pdir = copilot_home / "installed-plugins" / marketplace / name
    pdir.mkdir(parents=True, exist_ok=True)
    manifest: dict = {"name": name, "version": "0.1.0"}
    if codespace_plugins is not None:
        manifest["codespacePlugins"] = codespace_plugins
    if extra:
        manifest.update(extra)
    (pdir / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")


def _set_enabled(copilot_home: Path, *specs: str) -> None:
    """Write a user settings.json enabling the given '<name>@<mkt>' specs."""
    copilot_home.mkdir(parents=True, exist_ok=True)
    (copilot_home / "settings.json").write_text(
        json.dumps({"enabledPlugins": {s: True for s in specs}}), encoding="utf-8"
    )


# --------------------------------------------------------------------------
# repo_matches
# --------------------------------------------------------------------------

def test_repo_matches_empty_is_global():
    assert repo_matches((), "odsp-microsoft/odsp-web") is True
    assert repo_matches((), None) is True


def test_repo_matches_exact_case_insensitive():
    assert repo_matches(("odsp-microsoft/odsp-web",), "ODSP-Microsoft/ODSP-Web")
    assert not repo_matches(("odsp-microsoft/odsp-web",), "other/repo")


def test_repo_matches_glob():
    assert repo_matches(("odsp-microsoft/*",), "odsp-microsoft/odsp-web")
    assert not repo_matches(("odsp-microsoft/*",), "contoso/app")


def test_repo_matches_unknown_repo_only_global():
    assert repo_matches(("odsp-microsoft/odsp-web",), None) is False


# --------------------------------------------------------------------------
# enabled_plugin_names / iteration
# --------------------------------------------------------------------------

def test_enabled_plugin_names_none_when_absent(tmp_path):
    assert enabled_plugin_names(tmp_path) is None


def test_enabled_plugin_names_strips_marketplace(tmp_path):
    _set_enabled(tmp_path, "repo-odsp-web@dev-tmichon", "agent-bridge@copilot-extensions")
    assert enabled_plugin_names(tmp_path) == {"repo-odsp-web", "agent-bridge"}


def test_iter_installed_manifests(tmp_path):
    _install_plugin(tmp_path, "dev-tmichon", "repo-odsp-web")
    _install_plugin(tmp_path, "copilot-extensions", "agent-bridge")
    names = {n for n, _d, _m in iter_installed_manifests(tmp_path)}
    assert names == {"repo-odsp-web", "agent-bridge"}


# --------------------------------------------------------------------------
# resolve_codespace_plugins
# --------------------------------------------------------------------------

def _harness_with_repo_odsp_web(tmp_path) -> Path:
    """A harness where repo-odsp-web (enabled) declares odsp-web-codespace."""
    _install_plugin(
        tmp_path,
        "dev-tmichon",
        "repo-odsp-web",
        codespace_plugins=[
            {
                "source": "odsp-web-codespace@dev-tmichon",
                "enable": True,
                "forWorkspaceRepo": "odsp-microsoft/odsp-web",
            }
        ],
    )
    _set_enabled(tmp_path, "repo-odsp-web@dev-tmichon")
    return tmp_path


def test_repo_scoped_included_on_match(tmp_path):
    home = _harness_with_repo_odsp_web(tmp_path)
    specs = resolve_codespace_plugins("odsp-microsoft/odsp-web", copilot_home=home)
    assert [s.source for s in specs] == ["odsp-web-codespace@dev-tmichon"]
    assert specs[0].enable is True
    assert specs[0].declared_by == ("repo-odsp-web",)
    assert specs[0].is_global is False


def test_repo_scoped_excluded_on_mismatch(tmp_path):
    home = _harness_with_repo_odsp_web(tmp_path)
    assert resolve_codespace_plugins("contoso/app", copilot_home=home) == []


def test_repo_scoped_excluded_when_repo_unknown(tmp_path):
    home = _harness_with_repo_odsp_web(tmp_path)
    assert resolve_codespace_plugins(None, copilot_home=home) == []


def test_global_entry_always_included(tmp_path):
    _install_plugin(
        tmp_path,
        "copilot-extensions",
        "agent-codespaces",
        codespace_plugins=[{"source": "host-comm@copilot-extensions"}],
    )
    _set_enabled(tmp_path, "agent-codespaces@copilot-extensions")
    for repo in ("odsp-microsoft/odsp-web", None, "any/thing"):
        specs = resolve_codespace_plugins(repo, copilot_home=tmp_path)
        assert [s.source for s in specs] == ["host-comm@copilot-extensions"]
        assert specs[0].is_global is True
        assert specs[0].enable is True  # defaults to True


def test_only_enabled_filters_disabled_declarer(tmp_path):
    home = _harness_with_repo_odsp_web(tmp_path)
    # Overwrite settings so repo-odsp-web is NOT enabled.
    _set_enabled(home, "agent-bridge@copilot-extensions")
    assert resolve_codespace_plugins(
        "odsp-microsoft/odsp-web", copilot_home=home
    ) == []
    # ...but --all (only_enabled=False) still sees it.
    specs = resolve_codespace_plugins(
        "odsp-microsoft/odsp-web", copilot_home=home, only_enabled=False
    )
    assert [s.source for s in specs] == ["odsp-web-codespace@dev-tmichon"]


def test_no_settings_means_no_enablement_filter(tmp_path):
    # Declarer installed, but no settings.json at all -> cannot determine
    # enablement -> do not filter it out.
    _install_plugin(
        tmp_path,
        "dev-tmichon",
        "repo-odsp-web",
        codespace_plugins=[
            {"source": "odsp-web-codespace@dev-tmichon",
             "forWorkspaceRepo": "odsp-microsoft/odsp-web"}
        ],
    )
    specs = resolve_codespace_plugins("odsp-microsoft/odsp-web", copilot_home=tmp_path)
    assert [s.source for s in specs] == ["odsp-web-codespace@dev-tmichon"]


def test_dedup_merges_sources_and_enable(tmp_path):
    # Two enabled harness plugins declare the same source; one install-only,
    # one enable -> merged enable True, both recorded as declarers.
    _install_plugin(
        tmp_path, "dev-tmichon", "repo-a",
        codespace_plugins=[{"source": "shared@dev-tmichon", "enable": False}],
    )
    _install_plugin(
        tmp_path, "dev-tmichon", "repo-b",
        codespace_plugins=[{"source": "shared@dev-tmichon", "enable": True}],
    )
    _set_enabled(tmp_path, "repo-a@dev-tmichon", "repo-b@dev-tmichon")
    specs = resolve_codespace_plugins(None, copilot_home=tmp_path)
    assert len(specs) == 1
    assert specs[0].source == "shared@dev-tmichon"
    assert specs[0].enable is True
    assert set(specs[0].declared_by) == {"repo-a", "repo-b"}


def test_malformed_entries_ignored(tmp_path):
    _install_plugin(
        tmp_path, "dev-tmichon", "repo-odsp-web",
        codespace_plugins=[
            "not-an-object",
            {"no_source": True},
            {"source": ""},
            {"source": "ok@dev-tmichon"},
        ],
    )
    _set_enabled(tmp_path, "repo-odsp-web@dev-tmichon")
    specs = resolve_codespace_plugins(None, copilot_home=tmp_path)
    assert [s.source for s in specs] == ["ok@dev-tmichon"]


def test_codespace_plugins_not_a_list_ignored(tmp_path):
    _install_plugin(
        tmp_path, "dev-tmichon", "repo-odsp-web",
        codespace_plugins=None,  # field absent
    )
    _install_plugin(
        tmp_path, "dev-tmichon", "repo-bad",
        extra={"codespacePlugins": "oops-a-string"},
    )
    _set_enabled(tmp_path, "repo-odsp-web@dev-tmichon", "repo-bad@dev-tmichon")
    assert resolve_codespace_plugins(None, copilot_home=tmp_path) == []


def test_spec_to_dict_roundtrip():
    spec = CodespacePluginSpec(
        source="x@dev-tmichon",
        enable=False,
        for_workspace_repo=("owner/repo",),
        declared_by=("repo-x",),
    )
    d = spec.to_dict()
    assert d == {
        "source": "x@dev-tmichon",
        "enable": False,
        "forWorkspaceRepo": ["owner/repo"],
        "declaredBy": ["repo-x"],
    }


# --------------------------------------------------------------------------
# Harness-plugin guard (never inject a *-harness* plugin into a CodeSpace)
# --------------------------------------------------------------------------

def test_is_harness_plugin():
    assert is_harness_plugin("odsp-web-harness@dev-tmichon") is True
    assert is_harness_plugin("odsp-web-harness-status@m") is True
    assert is_harness_plugin("odsp-web-agent@m") is False
    assert is_harness_plugin("odsp-web-agent-development@m") is False
    assert is_harness_plugin("agent-codespaces@copilot-extensions") is False


def test_resolve_drops_harness_declarations(tmp_path):
    # A harness plugin mis-declared in codespacePlugins must be filtered out.
    _install_plugin(
        tmp_path, "dev-tmichon", "odsp-web-harness",
        codespace_plugins=[
            {"source": "odsp-web-harness@dev-tmichon"},   # dropped
            {"source": "odsp-web-agent@dev-tmichon"},     # kept
        ],
    )
    _set_enabled(tmp_path, "odsp-web-harness@dev-tmichon")
    specs = resolve_codespace_plugins("odsp-microsoft/odsp-web", copilot_home=tmp_path)
    assert [s.source for s in specs] == ["odsp-web-agent@dev-tmichon"]


# --------------------------------------------------------------------------
# Operator-declared globals (codespaces.yaml `codespace_plugins`)
# --------------------------------------------------------------------------

def test_parse_operator_plugins_drops_harness_and_parses():
    from agent_codespaces.codespace_plugins import parse_operator_plugins
    specs = parse_operator_plugins([
        {"source": "agent-worktrees@copilot-extensions"},
        {"source": "efforts@copilot-extensions", "enable": True},
        {"source": "foo-harness@dev-tmichon"},          # dropped
        "not-a-dict",                                     # ignored
    ])
    assert [s.source for s in specs] == [
        "agent-worktrees@copilot-extensions",
        "efforts@copilot-extensions",
    ]
    assert all(s.declared_by == ("codespaces.yaml",) for s in specs)
    assert all(s.is_global for s in specs)  # no forWorkspaceRepo -> global


def test_extra_specs_merged_as_global(tmp_path):
    # No installed harness plugins; operator declares two globals.
    from agent_codespaces.codespace_plugins import parse_operator_plugins
    extra = parse_operator_plugins([
        {"source": "agent-worktrees@copilot-extensions"},
        {"source": "efforts@copilot-extensions"},
    ])
    specs = resolve_codespace_plugins(
        "odsp-microsoft/odsp-web-codespaces", copilot_home=tmp_path, extra_specs=extra
    )
    assert [s.source for s in specs] == [
        "agent-worktrees@copilot-extensions",
        "efforts@copilot-extensions",
    ]


def test_extra_specs_union_with_swept(tmp_path):
    from agent_codespaces.codespace_plugins import parse_operator_plugins
    _install_plugin(
        tmp_path, "dev-tmichon", "odsp-web-harness",
        codespace_plugins=[{"source": "odsp-web-agent@dev-tmichon",
                            "forWorkspaceRepo": "odsp-microsoft/odsp-web*"}],
    )
    _set_enabled(tmp_path, "odsp-web-harness@dev-tmichon")
    extra = parse_operator_plugins([{"source": "agent-worktrees@copilot-extensions"}])
    specs = resolve_codespace_plugins(
        "odsp-microsoft/odsp-web-codespaces", copilot_home=tmp_path, extra_specs=extra
    )
    assert [s.source for s in specs] == [
        "agent-worktrees@copilot-extensions",     # sorted() order
        "odsp-web-agent@dev-tmichon",
    ]


def test_extra_specs_respect_repo_filter(tmp_path):
    from agent_codespaces.codespace_plugins import parse_operator_plugins
    extra = parse_operator_plugins([
        {"source": "x@mkt", "forWorkspaceRepo": "contoso/*"},  # non-matching
    ])
    specs = resolve_codespace_plugins(
        "odsp-microsoft/odsp-web", copilot_home=tmp_path, extra_specs=extra
    )
    assert specs == []


# --------------------------------------------------------------------------
# Repo-scoped enablement (enabled_names override) -- Workstream A
# --------------------------------------------------------------------------

def test_plugin_names_from_enabled():
    assert plugin_names_from_enabled({"a@m": True, "b@m": False, "c@m": True}) == {"a", "c"}
    assert plugin_names_from_enabled({}) == set()
    assert plugin_names_from_enabled(None) is None
    assert plugin_names_from_enabled("not-a-dict") is None


def test_enabled_names_override_supersedes_user_settings(tmp_path):
    # A non-harness plugin declares a codespacePlugins entry.
    _install_plugin(
        tmp_path, "copilot-extensions", "documenting-packages",
        codespace_plugins=[{"source": "odsp-web-agent@dev-tmichon"}],
    )
    # User settings *enables* the declaring plugin ...
    _set_enabled(tmp_path, "documenting-packages@copilot-extensions")

    # ... but a repo-scoped enabled_names that omits it wins -> filtered out
    # (proves the override is consulted instead of user settings.json).
    specs = resolve_codespace_plugins(
        "odsp-microsoft/odsp-web", copilot_home=tmp_path, enabled_names=set()
    )
    assert specs == []

    # And when the repo-scoped set includes it, the entry is injected.
    specs = resolve_codespace_plugins(
        "odsp-microsoft/odsp-web", copilot_home=tmp_path,
        enabled_names={"documenting-packages"},
    )
    assert [s.source for s in specs] == ["odsp-web-agent@dev-tmichon"]
