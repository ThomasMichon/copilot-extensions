from __future__ import annotations

import json
from pathlib import Path

from dropin_registry import ScanAuthority

from plugin_activation import current_declarations


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_current_native_local_overrides_user_and_claude(tmp_path: Path) -> None:
    home, repo = tmp_path / "home", tmp_path / "repo"
    write(home / ".copilot" / "settings.json", {
        "enabledPlugins": {"sample@remote": True, "other@remote": True},
        "extraKnownMarketplaces": {"remote": {"source": {"source": "github", "repo": "a/b"}}},
    })
    write(repo / ".claude" / "settings.local.json", {
        "enabledPlugins": {"sample@remote": True},
        "extraKnownMarketplaces": {"local": {"source": {"source": "directory", "path": "./old"}}},
    })
    write(repo / ".github" / "copilot" / "settings.local.json", {
        "enabledPlugins": {"sample@remote": False},
        "extraKnownMarketplaces": {"local": {"source": {"source": "directory", "path": "./new"}}},
    })
    result = current_declarations(home=home, project_root=repo)
    assert result.authority is ScanAuthority.COMPLETE
    assert result.settings.enabled == {"sample@remote": False, "other@remote": True}
    assert result.settings.marketplaces["local"]["source"]["path"] == "./new"
    assert result.marketplace_bases == {"remote": home / ".copilot", "local": repo}


def test_invalid_current_settings_cannot_become_user_fallback(tmp_path: Path) -> None:
    home, repo = tmp_path / "home", tmp_path / "repo"
    write(home / ".copilot" / "settings.json", {"enabledPlugins": {"sample@remote": True}})
    write(repo / ".github" / "copilot" / "settings.json", {"enabledPlugins": []})
    result = current_declarations(home=home, project_root=repo)
    assert result.authority is ScanAuthority.INDETERMINATE
    assert result.findings


def test_missing_declarations_are_read_only_absence(tmp_path: Path) -> None:
    result = current_declarations(home=tmp_path / "home", project_root=tmp_path / "repo")
    assert result.authority is ScanAuthority.ABSENT
    assert result.settings.enabled == {}
    assert list(tmp_path.iterdir()) == []
