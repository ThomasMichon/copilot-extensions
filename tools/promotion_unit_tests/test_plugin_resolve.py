"""Expose the original local-resolution units without their subprocess case."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIBRARY = ROOT / "libs/plugin-resolve"


def _load_source(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


_load_source("plugin_resolve", LIBRARY / "src/plugin_resolve/__init__.py")
_original = _load_source(
    "promotion_original_plugin_resolve", LIBRARY / "tests/test_plugin_resolve.py"
)
for _name, _function in vars(_original).items():
    if (
        _name.startswith("test_")
        and _name != "test_import_fails_with_unknown_location_when_init_missing"
    ):
        assert _name not in globals(), f"duplicate original resolution test: {_name}"
        globals()[_name] = _function
