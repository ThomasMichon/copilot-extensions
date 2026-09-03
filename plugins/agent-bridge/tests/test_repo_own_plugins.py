"""Tests for repo_own_plugins -- staging a repo's OWN enabledPlugins as
per-launch ``--plugin-dir`` args (dotfiles#905).

Verifies the leak-safe backstop: an *enabled-but-uninstalled* plugin (fork /
fresh-machine case) is staged via ``--plugin-dir`` resolved from a local
marketplace; an *installed* plugin is NOT re-staged (no double-load); and bad
input fails safe. No global copilot config is ever read or written.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_bridge import repo_own_plugins
from agent_bridge.repo_own_plugins import repo_plugin_dir_args


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
