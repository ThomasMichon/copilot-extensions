"""Tests for plugin_resolve -- Copilot CLI + Claude plugin resolution."""

from __future__ import annotations

import json
from pathlib import Path

from plugin_resolve import (
    load_marketplace,
    marketplace_manifest_path,
    plugin_dir,
    read_repo_settings,
    resolve_repo_plugins,
    split_source,
)


def _w(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# read_repo_settings -- native-first, Claude fallback.
# ---------------------------------------------------------------------------

def test_settings_native_only(tmp_path):
    _w(tmp_path / ".github" / "copilot" / "settings.json", {
        "extraKnownMarketplaces": {"mp": {"source": {"source": "directory", "path": "./.ai"}}},
        "enabledPlugins": {"a@mp": True, "off@mp": False},
    })
    s = read_repo_settings(tmp_path)
    assert s.enabled == {"a@mp": True, "off@mp": False}
    assert "mp" in s.marketplaces
    assert s.enabled_sources() == ["a@mp"]


def test_settings_claude_fallback(tmp_path):
    _w(tmp_path / ".claude" / "settings.json", {
        "extraKnownMarketplaces": {"spo": {"source": {"source": "directory", "path": "./.ai"}}},
        "enabledPlugins": {"cps@spo": True},
    })
    s = read_repo_settings(tmp_path)
    assert s.enabled["cps@spo"] is True
    assert "spo" in s.marketplaces


def test_settings_native_wins_over_claude(tmp_path):
    _w(tmp_path / ".claude" / "settings.json", {"enabledPlugins": {"cap@mp": False}})
    _w(tmp_path / ".github" / "copilot" / "settings.json", {"enabledPlugins": {"cap@mp": True}})
    s = read_repo_settings(tmp_path)
    assert s.enabled["cap@mp"] is True  # native wins


def test_settings_local_overrides_base(tmp_path):
    _w(tmp_path / ".claude" / "settings.json", {"enabledPlugins": {"cap@mp": False}})
    _w(tmp_path / ".claude" / "settings.local.json", {"enabledPlugins": {"cap@mp": True}})
    s = read_repo_settings(tmp_path)
    assert s.enabled["cap@mp"] is True


def test_settings_missing_is_empty(tmp_path):
    s = read_repo_settings(tmp_path / "nope")
    assert s.enabled == {} and s.marketplaces == {}


def test_split_source():
    assert split_source("name@market") == ("name", "market")
    assert split_source("bare") == ("bare", "")
    assert split_source("") == ("", "")


# ---------------------------------------------------------------------------
# Marketplace manifest location + plugin-dir resolution.
# ---------------------------------------------------------------------------

def _make_ai_marketplace(root: Path, name: str, plugin: str) -> None:
    """The `.ai` / SPO.Core layout: manifest at .claude-plugin, plugin under root."""
    _w(root / ".claude-plugin" / "marketplace.json", {
        "name": name,
        "plugins": [{"name": plugin, "source": f"./{plugin}"}],
    })
    _w(root / plugin / ".claude-plugin" / "plugin.json", {"name": plugin})


def _make_native_marketplace(root: Path, name: str, plugin: str) -> None:
    """The Copilot-native layout: manifest at .github/plugin, plugin.json at root."""
    _w(root / ".github" / "plugin" / "marketplace.json", {
        "name": name,
        "plugins": [{"name": plugin, "source": f"plugins/{plugin}"}],
    })
    _w(root / "plugins" / plugin / "plugin.json", {"name": plugin})


def test_manifest_path_prefers_native(tmp_path):
    _w(tmp_path / ".github" / "plugin" / "marketplace.json", {"name": "n", "plugins": []})
    _w(tmp_path / ".claude-plugin" / "marketplace.json", {"name": "c", "plugins": []})
    assert marketplace_manifest_path(tmp_path) == (
        tmp_path / ".github" / "plugin" / "marketplace.json"
    )


def test_load_ai_marketplace_and_plugin_dir(tmp_path):
    _make_ai_marketplace(tmp_path, "dotfiles-plugins", "generating-connect")
    mp = load_marketplace(tmp_path)
    assert mp is not None and mp.name == "dotfiles-plugins"
    d = plugin_dir(mp, "generating-connect")
    assert d == (tmp_path / "generating-connect").resolve()


def test_load_native_marketplace_and_plugin_dir(tmp_path):
    _make_native_marketplace(tmp_path, "mp", "hello")
    mp = load_marketplace(tmp_path)
    assert mp is not None
    assert plugin_dir(mp, "hello") == (tmp_path / "plugins" / "hello").resolve()


def test_plugin_root_prefix(tmp_path):
    _w(tmp_path / ".claude-plugin" / "marketplace.json", {
        "name": "mp",
        "metadata": {"pluginRoot": "./plugins"},
        "plugins": [{"name": "p", "source": "p"}],  # -> ./plugins/p
    })
    _w(tmp_path / "plugins" / "p" / ".claude-plugin" / "plugin.json", {"name": "p"})
    mp = load_marketplace(tmp_path)
    assert plugin_dir(mp, "p") == (tmp_path / "plugins" / "p").resolve()


def test_object_source_is_not_local(tmp_path):
    _w(tmp_path / ".claude-plugin" / "marketplace.json", {
        "name": "mp",
        "plugins": [{"name": "remote", "source": {"source": "github", "repo": "o/r"}}],
    })
    mp = load_marketplace(tmp_path)
    assert plugin_dir(mp, "remote") is None


def test_plugin_dir_none_without_manifest(tmp_path):
    _w(tmp_path / ".claude-plugin" / "marketplace.json", {
        "name": "mp",
        "plugins": [{"name": "p", "source": "./p"}],
    })
    # no ./p/plugin.json created
    mp = load_marketplace(tmp_path)
    assert plugin_dir(mp, "p") is None


def test_load_marketplace_missing(tmp_path):
    assert load_marketplace(tmp_path / "nope") is None


# ---------------------------------------------------------------------------
# resolve_repo_plugins -- the high-level answer.
# ---------------------------------------------------------------------------

def test_resolve_repo_plugins_ai_directory(tmp_path):
    repo = tmp_path / "repo"
    _make_ai_marketplace(repo / ".ai", "dotfiles-plugins", "generating-connect")
    _w(repo / ".github" / "copilot" / "settings.json", {
        "extraKnownMarketplaces": {
            "dotfiles-plugins": {"source": {"source": "directory", "path": "./.ai"}},
        },
        "enabledPlugins": {"generating-connect@dotfiles-plugins": True},
    })
    res = resolve_repo_plugins(repo)
    assert "generating-connect@dotfiles-plugins" in res.resolved
    assert res.resolved["generating-connect@dotfiles-plugins"] == (
        (repo / ".ai" / "generating-connect").resolve()
    )
    assert res.unresolved == []


def test_resolve_repo_plugins_claude_settings(tmp_path):
    repo = tmp_path / "repo"
    _make_ai_marketplace(repo / ".ai", "spo", "cps")
    _w(repo / ".claude" / "settings.json", {
        "extraKnownMarketplaces": {"spo": {"source": {"source": "directory", "path": "./.ai"}}},
        "enabledPlugins": {"cps@spo": True},
    })
    res = resolve_repo_plugins(repo)
    assert res.resolved["cps@spo"] == (repo / ".ai" / "cps").resolve()


def test_resolve_repo_plugins_remote_is_unresolved(tmp_path):
    repo = tmp_path / "repo"
    _w(repo / ".github" / "copilot" / "settings.json", {
        "extraKnownMarketplaces": {"gh": {"source": {"source": "github", "repo": "o/r"}}},
        "enabledPlugins": {"x@gh": True},
    })
    res = resolve_repo_plugins(repo)
    assert res.resolved == {}
    assert res.unresolved == ["x@gh"]


def test_resolve_repo_plugins_disabled_skipped(tmp_path):
    repo = tmp_path / "repo"
    _make_ai_marketplace(repo / ".ai", "mp", "p")
    _w(repo / ".github" / "copilot" / "settings.json", {
        "extraKnownMarketplaces": {"mp": {"source": {"source": "directory", "path": "./.ai"}}},
        "enabledPlugins": {"p@mp": False},
    })
    res = resolve_repo_plugins(repo)
    assert res.resolved == {} and res.unresolved == []
