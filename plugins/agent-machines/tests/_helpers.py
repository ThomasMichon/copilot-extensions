"""Shared fixtures: write a requirement package to a temp repo tree."""

from __future__ import annotations

from pathlib import Path

import yaml


def write_package(repo_root: Path, filename: str, data: dict) -> Path:
    state = repo_root / ".github" / "machine-state"
    state.mkdir(parents=True, exist_ok=True)
    path = state / filename
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def enable_plugin(repo_root: Path) -> None:
    cfg = repo_root / ".github" / "copilot"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "settings.json").write_text(
        '{"enabledPlugins": {"agent-machines@copilot-extensions": true}}', encoding="utf-8"
    )


def base_package(name: str = "acme/copilot-defaults", **over) -> dict:
    data = {
        "schema_version": 1,
        "package": name,
        "gate": ["box-1"],
        "manage": {
            "copilot.settings": {
                "disposition": "enforce",
                "values": {"model": "opus", "effortLevel": "high"},
            },
            "copilot.permissions": {"disposition": "ensure-present"},
        },
    }
    data.update(over)
    return data
