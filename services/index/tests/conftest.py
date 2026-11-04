from __future__ import annotations

from pathlib import Path
from typing import Any

import os

import pytest

from agent_index_service.config import parse_config


@pytest.fixture
def distribution_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    # Nested wheel/sdist paths need room below Windows' legacy path limit.
    return tmp_path_factory.mktemp("d")


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in list(os.environ):
        if name.startswith("AGENT_INDEX_") or name == "COPILOT_EXTENSIONS_CONTEXT":
            monkeypatch.delenv(name)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("AGENT_WORKTREES_HOME", str(tmp_path / "no-registry"))
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    config = parse_config({
        "schema_version": 1,
        "home": str(tmp_path / "service"),
        "sources": [{"name": "git:fixture", "path": str(tmp_path / "repo")}],
    }, tmp_path / "host.yaml")
    for name, value in config.core_environment().items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def config_data(tmp_path: Path) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "home": str(tmp_path / "service"),
        "sources": [{"name": "git:fixture", "path": str(tmp_path / "repo")}],
    }


@pytest.fixture
def config_file(tmp_path: Path, config_data: dict[str, Any]) -> Path:
    import yaml

    path = tmp_path / "host.yaml"
    path.write_text(yaml.safe_dump(config_data), encoding="utf-8")
    return path
