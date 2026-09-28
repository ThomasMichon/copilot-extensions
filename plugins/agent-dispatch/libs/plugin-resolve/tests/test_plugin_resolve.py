"""Tests for plugin_resolve -- Copilot CLI + Claude plugin resolution."""

from __future__ import annotations

import json
from pathlib import Path

from plugin_resolve import (
    MarketplaceSourceKind,
    load_marketplace,
    marketplace_manifest_path,
    marketplace_source_kind,
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


def test_settings_non_boolean_enablement_is_not_truthy(tmp_path):
    _w(
        tmp_path / ".github" / "copilot" / "settings.json",
        {"enabledPlugins": {"quoted@mp": "false", "numeric@mp": 1}},
    )
    assert read_repo_settings(tmp_path).enabled == {}


def test_marketplace_source_classification_is_typed():
    settings = read_repo_settings(Path("missing"))
    settings.marketplaces.update(
        {
            "local": {"source": {"source": "directory", "path": "./.ai"}},
            "remote": {"source": {"source": "github", "repo": "owner/repo"}},
            "bad": {"source": {"source": "unsupported"}},
        }
    )
    assert (
        marketplace_source_kind("local", settings)
        is MarketplaceSourceKind.LOCAL
    )
    assert (
        marketplace_source_kind("remote", settings)
        is MarketplaceSourceKind.REMOTE
    )
    assert (
        marketplace_source_kind("bad", settings)
        is MarketplaceSourceKind.INVALID
    )


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


def test_marketplace_name_must_be_a_string(tmp_path):
    _w(
        tmp_path / ".claude-plugin" / "marketplace.json",
        {"name": 123, "plugins": []},
    )
    assert load_marketplace(tmp_path) is None


def test_plugin_entry_name_must_be_a_string(tmp_path):
    _w(
        tmp_path / ".claude-plugin" / "marketplace.json",
        {"name": "mp", "plugins": [{"name": 123, "source": "p"}]},
    )
    _w(tmp_path / "p" / "plugin.json", {"name": "123"})
    mp = load_marketplace(tmp_path)
    assert mp is not None
    assert mp.plugins == {}
    assert plugin_dir(mp, "123") is None


def test_plugin_source_cannot_escape_marketplace(tmp_path):
    outside = tmp_path / "outside"
    market = tmp_path / "market"
    _w(outside / "plugin.json", {"name": "p"})
    _w(
        market / ".claude-plugin" / "marketplace.json",
        {
            "name": "mp",
            "plugins": [{"name": "p", "source": "../../outside"}],
        },
    )
    mp = load_marketplace(market)
    assert plugin_dir(mp, "p") is None


def test_plugin_root_cannot_escape_marketplace(tmp_path):
    outside = tmp_path / "outside"
    market = tmp_path / "market"
    _w(outside / "p" / "plugin.json", {"name": "p"})
    _w(
        market / ".claude-plugin" / "marketplace.json",
        {
            "name": "mp",
            "metadata": {"pluginRoot": "../../outside"},
            "plugins": [{"name": "p", "source": "p"}],
        },
    )
    mp = load_marketplace(market)
    assert plugin_dir(mp, "p") is None


def test_plugin_symlink_cannot_escape_marketplace(tmp_path):
    outside = tmp_path / "outside"
    market = tmp_path / "market"
    _w(outside / "plugin.json", {"name": "p"})
    _w(
        market / ".claude-plugin" / "marketplace.json",
        {"name": "mp", "plugins": [{"name": "p", "source": "p"}]},
    )
    try:
        (market / "p").symlink_to(outside, target_is_directory=True)
    except OSError:
        return
    mp = load_marketplace(market)
    assert plugin_dir(mp, "p") is None


def test_plugin_manifest_name_must_match_marketplace_entry(tmp_path):
    _w(
        tmp_path / ".claude-plugin" / "marketplace.json",
        {"name": "mp", "plugins": [{"name": "p", "source": "p"}]},
    )
    _w(tmp_path / "p" / "plugin.json", {"name": "other"})
    mp = load_marketplace(tmp_path)
    assert plugin_dir(mp, "p") is None


def test_duplicate_plugin_name_is_ambiguous(tmp_path):
    _w(
        tmp_path / ".claude-plugin" / "marketplace.json",
        {
            "name": "mp",
            "plugins": [
                {"name": "p", "source": "first"},
                {"name": "p", "source": "second"},
            ],
        },
    )
    _w(tmp_path / "first" / "plugin.json", {"name": "p"})
    _w(tmp_path / "second" / "plugin.json", {"name": "p"})
    mp = load_marketplace(tmp_path)
    assert mp is not None and mp.duplicates == frozenset({"p"})
    assert plugin_dir(mp, "p") is None


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


def test_resolve_repo_plugins_requires_marketplace_identity(tmp_path):
    repo = tmp_path / "repo"
    _make_ai_marketplace(repo / ".ai", "other", "p")
    _w(
        repo / ".github" / "copilot" / "settings.json",
        {
            "extraKnownMarketplaces": {
                "expected": {
                    "source": {"source": "directory", "path": "./.ai"}
                }
            },
            "enabledPlugins": {"p@expected": True},
        },
    )
    result = resolve_repo_plugins(repo)
    assert result.resolved == {}
    assert result.unresolved == ["p@expected"]


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


# ---------------------------------------------------------------------------
# Regression / characterization: transient namespace-package shadowing
# (copilot-extensions#3999, recurrence of #2174).
#
# install.ps1's `Invoke-VenvPackageInstall` reinstalls this vendored library
# in place into the CURRENTLY-ACTIVE versioned runtime slot (`uv pip install
# --reinstall-package agent-plugin-resolve ...`), and does so via a SEPARATE
# uv invocation from the dependent `agent-worktrees`/`plugin_activation`
# reinstall. uv's own reinstall of a single package is an uninstall-then-
# install, non-atomic. If a concurrently-running interpreter (e.g. another
# CLI session's binstub) imports `plugin_resolve` in the window after the old
# package's files (including `__init__.py`) are removed but before the new
# ones are written, Python resolves the still-present, now `__init__.py`-less
# directory as an implicit PEP 420 namespace package: it has no `__file__`
# and no top-level names, so any `from plugin_resolve import <name>` fails
# with `ImportError: cannot import name '<name>' from 'plugin_resolve'
# (unknown location)` -- regardless of which name is imported first, and
# regardless of whether the package's *content* actually changed. This test
# pins that failure mode so a fix (e.g. building into a fresh candidate slot
# and atomically swapping, rather than mutating a live active slot) can be
# verified against it.
# ---------------------------------------------------------------------------

def test_import_fails_with_unknown_location_when_init_missing(tmp_path):
    """Simulate the mid-reinstall instant: the package dir exists on
    `sys.path` but its `__init__.py` has already been removed (as uv's
    uninstall step does) and the replacement has not yet been written. Must
    run in a genuinely isolated subprocess -- in-process, the test runner's
    own regularly-installed `plugin_resolve` (found later on `sys.path`)
    would win over the torn directory (a regular package with `__init__.py`
    always wins over an earlier namespace-package portion), masking the
    bug. This reproduces the exact ImportError shape from
    copilot-extensions#3999, not a ModuleNotFoundError or AttributeError."""
    import subprocess
    import sys

    torn_root = tmp_path / "torn-site-packages"
    pkg_dir = torn_root / "plugin_resolve"
    pkg_dir.mkdir(parents=True)
    # No __init__.py written yet -- this is the transient state between uv's
    # `rm -rf plugin_resolve/` and its re-extraction of the new wheel.
    (pkg_dir / "conventions.py").write_text(
        "MARKETPLACE_MANIFEST_RELS = ()\n", encoding="utf-8"
    )

    probe = (
        "import sys\n"
        f"sys.path = [{str(torn_root)!r}]\n"
        "try:\n"
        "    from plugin_resolve import MARKETPLACE_MANIFEST_RELS\n"
        "    print('NO-ERROR')\n"
        "except ImportError as e:\n"
        "    print(str(e))\n"
    )
    # -S: skip site initialization, so the real installed `plugin_resolve`
    # (from this interpreter's own site-packages) never enters `sys.path`
    # and can't mask the torn directory under test.
    result = subprocess.run(
        [sys.executable, "-S", "-c", probe],
        capture_output=True, text=True, check=True,
    )
    out = result.stdout.strip()
    assert out != "NO-ERROR", "torn package unexpectedly imported cleanly"
    # Assert the complete message, not a substring -- a substring match would
    # also pass for an unrelated message that merely mentions these two
    # phrases, silently accepting a shape change in the failure this test
    # exists to pin.
    assert out == (
        "cannot import name 'MARKETPLACE_MANIFEST_RELS' "
        "from 'plugin_resolve' (unknown location)"
    )
