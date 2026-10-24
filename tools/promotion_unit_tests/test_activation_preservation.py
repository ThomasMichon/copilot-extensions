"""Run original preservation assertions with synthetic, non-native file reads."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_source(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


for _library, _package in (
    ("dropin-registry", "dropin_registry"),
    ("plugin-resolve", "plugin_resolve"),
    ("single-instance-lease", "single_instance_lease"),
    ("plugin-activation", "plugin_activation"),
):
    _load_source(_package, ROOT / "libs" / _library / "src" / _package / "__init__.py")
_original = _load_source(
    "promotion_original_activation_preservation",
    ROOT / "libs/plugin-activation/tests/test_preservation.py",
)


@pytest.fixture(autouse=True)
def _synthetic_file_operations(monkeypatch):
    monkeypatch.setattr(_original.state, "open_binary_shared", lambda path: path.open("rb"))
    monkeypatch.setattr(_original.state, "_replace_state", os.replace)


for _name, _function in vars(_original).items():
    if _name.startswith("test_"):
        assert _name not in globals(), f"duplicate original preservation test: {_name}"
        globals()[_name] = _function
