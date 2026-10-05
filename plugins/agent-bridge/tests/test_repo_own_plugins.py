"""Tests for repo_own_plugins -- staging a repo's OWN enabledPlugins as
per-launch ``--plugin-dir`` args (dotfiles#905).

Verifies complete ACP stack composition: local marketplace plugins are staged
from their source directories, remote marketplace plugins fall back to installed
payloads, and bad input fails safe. No global Copilot config is read or written.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_bridge import related_plugins, repo_own_plugins
from agent_bridge.repo_own_plugins import repo_plugin_dir_args
from agent_bridge.transport import PluginRef


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _make_local_marketplace(root: Path, mp_name: str, plugin: str) -> None:
    """A minimal local-path marketplace with one plugin (name == source dir)."""
    _write(
        root / ".github" / "plugin" / "marketplace.json",
        {
            "name": mp_name,
            "plugins": [
                {"name": plugin, "version": "0.0.1", "source": f"plugins/{plugin}"}
            ],
        },
    )
    _write(root / "plugins" / plugin / "plugin.json", {"name": plugin, "version": "0.0.1"})


def _make_repo(anchor: Path, enabled: dict, marketplaces: dict) -> None:
    _write(
        anchor / ".github" / "copilot" / "settings.json",
        {"extraKnownMarketplaces": marketplaces, "enabledPlugins": enabled},
    )


@pytest.fixture(autouse=True)
def restore_installed_root():
    original = repo_own_plugins._INSTALLED
    try:
        yield
    finally:
        repo_own_plugins._INSTALLED = original


def test_stages_enabled_uninstalled_plugin_from_local_marketplace(tmp_path):
    mp = tmp_path / "market"
    _make_local_marketplace(mp, "tw", "hello")
    anchor = tmp_path / "repo"
    _make_repo(
        anchor,
        enabled={"hello@tw": True},
        marketplaces={"tw": {"source": {"source": "local", "path": str(mp)}}},
    )
    # Point installed-plugins at an empty dir so nothing is "installed".
    repo_own_plugins._INSTALLED = tmp_path / "installed"

    args = repo_plugin_dir_args(anchor)

    assert "--plugin-dir" in args
    assert str(mp / "plugins" / "hello") in args


def test_installed_local_plugin_is_staged_from_local_source(tmp_path):
    mp = tmp_path / "market"
    _make_local_marketplace(mp, "tw", "hello")
    anchor = tmp_path / "repo"
    _make_repo(
        anchor,
        enabled={"hello@tw": True},
        marketplaces={"tw": {"source": {"source": "local", "path": str(mp)}}},
    )
    # ACP ignores enabledPlugins, so the preferred local source remains explicit.
    installed = tmp_path / "installed"
    _write(installed / "tw" / "hello" / "plugin.json", {"name": "hello"})
    repo_own_plugins._INSTALLED = installed

    assert repo_plugin_dir_args(anchor) == [
        "--plugin-dir",
        str(mp / "plugins" / "hello"),
    ]


def test_installed_remote_marketplace_plugin_is_staged(tmp_path):
    anchor = tmp_path / "repo"
    _make_repo(
        anchor,
        enabled={"remote@ghmp": True},
        marketplaces={"ghmp": {"source": {"source": "github", "repo": "o/r"}}},
    )
    installed = tmp_path / "installed"
    _write(installed / "ghmp" / "remote" / "plugin.json", {"name": "remote"})
    repo_own_plugins._INSTALLED = installed

    args = repo_plugin_dir_args(anchor)

    assert args == ["--plugin-dir", str(installed / "ghmp" / "remote")]


def test_local_and_installed_remote_plugins_are_both_staged(tmp_path):
    anchor = tmp_path / "repo"
    local = anchor / ".ai"
    _make_local_marketplace(local, "local-mp", "local-plugin")
    _make_repo(
        anchor,
        enabled={
            "local-plugin@local-mp": True,
            "remote-plugin@remote-mp": True,
        },
        marketplaces={
            "local-mp": {"source": {"source": "directory", "path": str(local)}},
            "remote-mp": {
                "source": {"source": "github", "repo": "example/plugins"}
            },
        },
    )
    installed = tmp_path / "installed"
    _write(
        installed / "remote-mp" / "remote-plugin" / "plugin.json",
        {"name": "remote-plugin"},
    )
    repo_own_plugins._INSTALLED = installed

    assert repo_plugin_dir_args(anchor) == [
        "--plugin-dir",
        str(local / "plugins" / "local-plugin"),
        "--plugin-dir",
        str(installed / "remote-mp" / "remote-plugin"),
    ]


def test_installed_path_components_cannot_escape_inventory(tmp_path):
    anchor = tmp_path / "repo"
    _make_repo(
        anchor,
        enabled={"../plugin@remote-mp": True},
        marketplaces={
            "remote-mp": {
                "source": {"source": "github", "repo": "example/plugins"}
            }
        },
    )
    repo_own_plugins._INSTALLED = tmp_path / "installed"

    assert repo_plugin_dir_args(anchor) == []


def test_disabled_and_unavailable_are_not_staged(tmp_path):
    anchor = tmp_path / "repo"
    _make_repo(
        anchor,
        enabled={"off@tw": False, "remote@ghmp": True},
        marketplaces={"ghmp": {"source": {"source": "github", "repo": "o/r"}}},
    )
    repo_own_plugins._INSTALLED = tmp_path / "installed"

    # Disabled -> skipped; remote (non-local) unavailable -> not staged, no mutation.
    assert repo_plugin_dir_args(anchor) == []


def test_fail_safe_on_bad_input(tmp_path):
    assert repo_plugin_dir_args(None) == []
    assert repo_plugin_dir_args(tmp_path / "does-not-exist") == []
    # A repo without settings.json -> [].
    (tmp_path / "empty").mkdir()
    assert repo_plugin_dir_args(tmp_path / "empty") == []


# ---------------------------------------------------------------------------
# `.ai` local plugin marketplace (SPO.Core standard): `directory` source, a
# relative path resolved against the anchor, and `.claude-plugin` manifests.
# ---------------------------------------------------------------------------

def _make_ai_marketplace(anchor: Path, mp_name: str, plugin: str) -> None:
    """A repo-local ``.ai`` marketplace: manifest at .ai/.claude-plugin, plugin at
    .ai/<plugin>/.claude-plugin (the SPO.Core / dotfiles layout)."""
    _write(
        anchor / ".ai" / ".claude-plugin" / "marketplace.json",
        {
            "name": mp_name,
            "plugins": [
                {"name": plugin, "version": "0.1.0", "source": f"./{plugin}"}
            ],
        },
    )
    _write(
        anchor / ".ai" / plugin / ".claude-plugin" / "plugin.json",
        {"name": plugin, "version": "0.1.0"},
    )


def test_stages_ai_directory_marketplace_relative_path(tmp_path):
    anchor = tmp_path / "repo"
    _make_ai_marketplace(anchor, "dotfiles-plugins", "generating-connect")
    _make_repo(
        anchor,
        enabled={"generating-connect@dotfiles-plugins": True},
        # the `.ai` standard: a `directory` source with a repo-relative path
        marketplaces={
            "dotfiles-plugins": {"source": {"source": "directory", "path": "./.ai"}}
        },
    )
    repo_own_plugins._INSTALLED = tmp_path / "installed"

    args = repo_plugin_dir_args(anchor)

    assert "--plugin-dir" in args
    # relative ./.ai resolved against the anchor; plugin dir found via manifest
    assert str(anchor / ".ai" / "generating-connect") in args


def test_ai_installed_plugin_is_staged_from_local_source(tmp_path):
    anchor = tmp_path / "repo"
    _make_ai_marketplace(anchor, "dotfiles-plugins", "generating-connect")
    _make_repo(
        anchor,
        enabled={"generating-connect@dotfiles-plugins": True},
        marketplaces={
            "dotfiles-plugins": {"source": {"source": "directory", "path": "./.ai"}}
        },
    )
    # Mark installed via a `.claude-plugin/plugin.json`; local source still wins.
    installed = tmp_path / "installed"
    _write(
        installed / "dotfiles-plugins" / "generating-connect" / ".claude-plugin"
        / "plugin.json",
        {"name": "generating-connect"},
    )
    repo_own_plugins._INSTALLED = installed

    assert repo_plugin_dir_args(anchor) == [
        "--plugin-dir",
        str(anchor / ".ai" / "generating-connect"),
    ]


# ---------------------------------------------------------------------------
# Claude-convention settings fallback: a repo may declare its plugins in
# `.claude/settings.json` instead of `.github/copilot/settings.json`
# (Copilot-native preferred, Claude fallback).
# ---------------------------------------------------------------------------

def _make_repo_claude_settings(anchor: Path, enabled: dict, marketplaces: dict) -> None:
    _write(
        anchor / ".claude" / "settings.json",
        {"extraKnownMarketplaces": marketplaces, "enabledPlugins": enabled},
    )


def test_stages_from_claude_settings_when_no_native(tmp_path):
    anchor = tmp_path / "repo"
    _make_ai_marketplace(anchor, "dotfiles-plugins", "generating-connect")
    # Only a .claude/settings.json (no .github/copilot/settings.json).
    _make_repo_claude_settings(
        anchor,
        enabled={"generating-connect@dotfiles-plugins": True},
        marketplaces={
            "dotfiles-plugins": {"source": {"source": "directory", "path": "./.ai"}}
        },
    )
    repo_own_plugins._INSTALLED = tmp_path / "installed"

    args = repo_plugin_dir_args(anchor)

    assert "--plugin-dir" in args
    assert str(anchor / ".ai" / "generating-connect") in args


def test_native_settings_win_over_claude(tmp_path):
    anchor = tmp_path / "repo"
    _make_ai_marketplace(anchor, "mp", "cap")
    # Claude disables the plugin; native enables it (same key) -> native wins.
    _make_repo_claude_settings(
        anchor,
        enabled={"cap@mp": False},
        marketplaces={"mp": {"source": {"source": "directory", "path": "./.ai"}}},
    )
    _make_repo(
        anchor,
        enabled={"cap@mp": True},
        marketplaces={"mp": {"source": {"source": "directory", "path": "./.ai"}}},
    )
    repo_own_plugins._INSTALLED = tmp_path / "installed"

    args = repo_plugin_dir_args(anchor)

    # Native's enabled=True wins over Claude's False -> the plugin is staged.
    assert str(anchor / ".ai" / "cap") in args


def test_claude_settings_local_overrides_claude_base(tmp_path):
    anchor = tmp_path / "repo"
    _make_ai_marketplace(anchor, "mp", "cap")
    _write(
        anchor / ".claude" / "settings.json",
        {
            "extraKnownMarketplaces": {
                "mp": {"source": {"source": "directory", "path": "./.ai"}}
            },
            "enabledPlugins": {"cap@mp": False},
        },
    )
    _write(
        anchor / ".claude" / "settings.local.json",
        {"enabledPlugins": {"cap@mp": True}},  # local override flips it on
    )
    repo_own_plugins._INSTALLED = tmp_path / "installed"

    args = repo_plugin_dir_args(anchor)

    assert str(anchor / ".ai" / "cap") in args


# ---------------------------------------------------------------------------
# `related_plugin_dir_args` -- the local-loopback counterpart of a
# namespace-resolved target's ``extra_plugins`` staging: resolves a
# control-repo-declared related plugin against the dispatching machine's own
# control-plane anchors (no remote copy -- loopback shares this filesystem).
# ---------------------------------------------------------------------------

def test_related_plugin_resolved_from_declaring_anchor(tmp_path, monkeypatch):
    control_anchor = tmp_path / "control-repo"
    _make_local_marketplace(control_anchor / ".ai", "control-local", "enhancer")
    # The marketplace manifest above is Copilot-native shaped; point the
    # control anchor's own settings at it as a local marketplace.
    _make_repo(
        control_anchor,
        enabled={},
        marketplaces={
            "control-local": {
                "source": {"source": "directory", "path": str(control_anchor / ".ai")}
            }
        },
    )
    repo_own_plugins._INSTALLED = tmp_path / "installed"

    monkeypatch.setattr(
        related_plugins,
        "related_plugins_for_repo",
        lambda repo, anchors=None: [PluginRef("enhancer@control-local", enable=True)],
    )

    args = repo_own_plugins.related_plugin_dir_args(
        "target-repo", repo_roots=[control_anchor]
    )

    assert args == [
        "--plugin-dir", str(control_anchor / ".ai" / "plugins" / "enhancer"),
    ]


def test_related_plugin_falls_back_to_installed(tmp_path, monkeypatch):
    installed = tmp_path / "installed"
    _write(installed / "control-local" / "enhancer" / "plugin.json", {"name": "enhancer"})
    repo_own_plugins._INSTALLED = installed

    monkeypatch.setattr(
        related_plugins,
        "related_plugins_for_repo",
        lambda repo, anchors=None: [PluginRef("enhancer@control-local", enable=True)],
    )

    args = repo_own_plugins.related_plugin_dir_args("target-repo", repo_roots=[])

    assert args == ["--plugin-dir", str(installed / "control-local" / "enhancer")]


def test_related_plugin_unresolvable_is_skipped_not_raised(tmp_path, monkeypatch):
    repo_own_plugins._INSTALLED = tmp_path / "installed"
    monkeypatch.setattr(
        related_plugins,
        "related_plugins_for_repo",
        lambda repo, anchors=None: [PluginRef("ghost@nowhere", enable=True)],
    )

    assert repo_own_plugins.related_plugin_dir_args("target-repo", repo_roots=[]) == []


def test_related_plugin_no_refs_returns_empty(monkeypatch):
    monkeypatch.setattr(
        related_plugins, "related_plugins_for_repo", lambda repo, anchors=None: [],
    )
    assert repo_own_plugins.related_plugin_dir_args("target-repo") == []
    assert repo_own_plugins.related_plugin_dir_args(None) == []


def test_related_plugin_first_claiming_anchor_wins_even_if_broken(tmp_path, monkeypatch):
    # The FIRST repo_root declaring `control-local` as a local marketplace
    # claims it -- even if its declared plugin is missing -- and must not
    # fall through to a second root that also declares the same marketplace
    # name with the plugin actually present, nor to the installed payload.
    # Mirrors agent_codespaces.plugin_staging's shadowing-safe behavior.
    first_root = tmp_path / "first"
    _make_local_marketplace(first_root / ".ai", "control-local", "other-plugin")
    _make_repo(
        first_root,
        enabled={},
        marketplaces={
            "control-local": {
                "source": {"source": "directory", "path": str(first_root / ".ai")}
            }
        },
    )
    second_root = tmp_path / "second"
    _make_local_marketplace(second_root / ".ai", "control-local", "enhancer")
    _make_repo(
        second_root,
        enabled={},
        marketplaces={
            "control-local": {
                "source": {"source": "directory", "path": str(second_root / ".ai")}
            }
        },
    )
    installed = tmp_path / "installed"
    _write(installed / "control-local" / "enhancer" / "plugin.json", {"name": "enhancer"})
    repo_own_plugins._INSTALLED = installed

    monkeypatch.setattr(
        related_plugins,
        "related_plugins_for_repo",
        lambda repo, anchors=None: [PluginRef("enhancer@control-local", enable=True)],
    )

    args = repo_own_plugins.related_plugin_dir_args(
        "target-repo", repo_roots=[first_root, second_root],
    )

    assert args == []


def test_related_plugin_remote_first_anchor_blocks_local_second_anchor(tmp_path, monkeypatch):
    # The FIRST repo_root to declare `control-local` wins even when its own
    # declaration is non-local (remote/github) -- a SECOND root's local
    # declaration of the same marketplace name must never be consulted, and
    # resolution falls back to the installed payload instead (the only
    # option for a remote-sourced marketplace this function doesn't fetch).
    first_root = tmp_path / "first"
    _make_repo(
        first_root,
        enabled={},
        marketplaces={
            "control-local": {"source": {"source": "github", "repo": "o/r"}}
        },
    )
    second_root = tmp_path / "second"
    _make_local_marketplace(second_root / ".ai", "control-local", "enhancer")
    _make_repo(
        second_root,
        enabled={},
        marketplaces={
            "control-local": {
                "source": {"source": "directory", "path": str(second_root / ".ai")}
            }
        },
    )
    installed = tmp_path / "installed"
    _write(installed / "control-local" / "enhancer" / "plugin.json", {"name": "enhancer"})
    repo_own_plugins._INSTALLED = installed

    monkeypatch.setattr(
        related_plugins,
        "related_plugins_for_repo",
        lambda repo, anchors=None: [PluginRef("enhancer@control-local", enable=True)],
    )

    args = repo_own_plugins.related_plugin_dir_args(
        "target-repo", repo_roots=[first_root, second_root],
    )

    # Falls back to the installed payload (first anchor is remote-sourced,
    # second anchor's local declaration of the same name is never reached).
    assert args == ["--plugin-dir", str(installed / "control-local" / "enhancer")]


def test_related_plugin_rejects_manifest_with_mismatched_marketplace_identity(
    tmp_path, monkeypatch,
):
    # Regression (#5306 review): the settings.json entry may declare a
    # marketplace path whose OWN manifest self-identifies under a different
    # name (a stale/misconfigured declaration). Even if that mismatched
    # manifest happens to declare a plugin of the requested name, it must
    # never be trusted -- matching plugin_resolve.resolve_repo_plugins'
    # identical mp.name == marketplace check.
    anchor = tmp_path / "repo"
    # The manifest at this path self-identifies as "actually-different-mp",
    # not "control-local" -- the key settings.json declares it under.
    _make_local_marketplace(anchor / ".ai", "actually-different-mp", "enhancer")
    _make_repo(
        anchor,
        enabled={},
        marketplaces={
            "control-local": {
                "source": {"source": "directory", "path": str(anchor / ".ai")}
            }
        },
    )
    installed = tmp_path / "installed"
    repo_own_plugins._INSTALLED = installed

    monkeypatch.setattr(
        related_plugins,
        "related_plugins_for_repo",
        lambda repo, anchors=None: [PluginRef("enhancer@control-local", enable=True)],
    )

    args = repo_own_plugins.related_plugin_dir_args("target-repo", repo_roots=[anchor])

    assert args == []


def test_related_plugin_one_broken_reference_does_not_drop_the_rest(
    tmp_path, monkeypatch,
):
    # Regression (#5306 review): a reference that raises while resolving
    # (not just one that returns None) must be recorded as unresolved and
    # must not abort resolution of the remaining references.
    good_root = tmp_path / "good"
    _make_local_marketplace(good_root / ".ai", "control-local", "enhancer")
    _make_repo(
        good_root,
        enabled={},
        marketplaces={
            "control-local": {
                "source": {"source": "directory", "path": str(good_root / ".ai")}
            }
        },
    )
    repo_own_plugins._INSTALLED = tmp_path / "installed"

    monkeypatch.setattr(
        related_plugins,
        "related_plugins_for_repo",
        lambda repo, anchors=None: [
            PluginRef("before@control-local", enable=True),
            PluginRef("enhancer@control-local", enable=True),
            PluginRef("after@control-local", enable=True),
        ],
    )

    real_resolve = repo_own_plugins._resolve_ref_dir

    def _flaky(source, repo_roots):
        if source == "before@control-local":
            raise OSError("simulated filesystem error")
        return real_resolve(source, repo_roots)

    monkeypatch.setattr(repo_own_plugins, "_resolve_ref_dir", _flaky)

    args = repo_own_plugins.related_plugin_dir_args(
        "target-repo", repo_roots=[good_root],
    )

    assert args == ["--plugin-dir", str(good_root / ".ai" / "plugins" / "enhancer")]
