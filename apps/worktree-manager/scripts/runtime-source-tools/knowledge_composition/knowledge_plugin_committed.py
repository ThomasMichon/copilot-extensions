"""Read only the committed harness plugin settings tiers."""

from __future__ import annotations

import json
from pathlib import Path

from plugin_resolve.conventions import SETTINGS_RELS


def read_harness_committed_marketplaces(repo_dir: Path) -> dict[str, dict]:
    """Read only the harness's committed (non-local) marketplace declarations."""
    marketplaces: dict[str, dict] = {}
    for rel in SETTINGS_RELS:
        if rel[-1] == "settings.local.json":
            continue
        path = repo_dir.joinpath(*rel)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        raw_marketplaces = data.get("extraKnownMarketplaces")
        if isinstance(raw_marketplaces, dict):
            for name, definition in raw_marketplaces.items():
                if isinstance(definition, dict):
                    marketplaces[name] = definition
    return marketplaces


def read_harness_committed_enabled(repo_dir: Path) -> dict[str, bool]:
    """Read only the harness's committed (non-local) enabledPlugins values."""
    enabled: dict[str, bool] = {}
    for rel in SETTINGS_RELS:
        if rel[-1] == "settings.local.json":
            continue
        path = repo_dir.joinpath(*rel)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        raw_enabled = data.get("enabledPlugins")
        if isinstance(raw_enabled, dict):
            for name, value in raw_enabled.items():
                if isinstance(name, str) and isinstance(value, bool):
                    enabled[name] = value
    return enabled
