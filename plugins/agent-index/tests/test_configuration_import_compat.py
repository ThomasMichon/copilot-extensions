"""Configuration consumers need functional APIs, not newer annotation exports."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import configuration_layers
import pytest


def test_original_configuration_surface_can_load_native_preferences(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(configuration_layers, "Composition", raising=False)
    monkeypatch.delattr(configuration_layers, "Layer", raising=False)
    path = Path(__file__).resolve().parents[1] / "src" / "agent_index" / "configuration_layers.py"
    spec = importlib.util.spec_from_file_location("agent_index._legacy_preferences_fixture", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "owner_report", lambda *_args, **_kwargs: None)
    selected = tmp_path / module.cfg.REPO_CONFIG_RELPATH
    selected.parent.mkdir(parents=True)
    selected.write_text('{"indexer": {"machine": "fixture"}}', encoding="utf-8")
    result = module.load_repository_layers(tmp_path, parser=json.loads)
    assert result["values"]["indexer"] == {"machine": "fixture"}
