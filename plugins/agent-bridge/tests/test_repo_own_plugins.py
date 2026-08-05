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


def test_installed_plugin_not_restaged(tmp_path):
    mp = tmp_path / "market"
    _make_local_marketplace(mp, "tw", "hello")
    anchor = tmp_path / "repo"
    _make_repo(
        anchor,
        enabled={"hello@tw": True},
        marketplaces={"tw": {"source": {"source": "local", "path": str(mp)}}},
    )
    # Mark the plugin installed on disk -> must NOT be staged (avoids double-load).
    installed = tmp_path / "installed"
    _write(installed / "tw" / "hello" / "plugin.json", {"name": "hello"})
    repo_own_plugins._INSTALLED = installed

    assert repo_plugin_dir_args(anchor) == []


def test_disabled_and_unresolved_are_not_staged(tmp_path):
    anchor = tmp_path / "repo"
    _make_repo(
        anchor,
        enabled={"off@tw": False, "remote@ghmp": True},
        marketplaces={"ghmp": {"source": {"source": "github", "repo": "o/r"}}},
    )
    repo_own_plugins._INSTALLED = tmp_path / "installed"

    # Disabled -> skipped; remote (non-local) uninstalled -> not staged, no mutation.
    assert repo_plugin_dir_args(anchor) == []


def test_fail_safe_on_bad_input(tmp_path):
    assert repo_plugin_dir_args(None) == []
    assert repo_plugin_dir_args(tmp_path / "does-not-exist") == []
    # A repo without settings.json -> [].
    (tmp_path / "empty").mkdir()
    assert repo_plugin_dir_args(tmp_path / "empty") == []
