"""Expose the original drop-in registry unit bodies without copying them."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIBRARY = ROOT / "libs/dropin-registry"


def _load_source(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


_load_source("dropin_registry", LIBRARY / "src/dropin_registry/__init__.py")
_original = _load_source(
    "promotion_original_dropin_registry", LIBRARY / "tests/test_dropin_registry.py"
)
for _name, _function in vars(_original).items():
    if _name.startswith("test_"):
        assert _name not in globals(), f"duplicate original registry test: {_name}"
        globals()[_name] = _function
