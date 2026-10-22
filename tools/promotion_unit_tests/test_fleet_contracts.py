"""Expose the original portable contract unit bodies without copying them."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIBRARY = ROOT / "libs/fleet-contracts"


def _load_source(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


_load_source("fleet_contracts", LIBRARY / "src/fleet_contracts/__init__.py")
for _filename in ("test_records.py", "test_responses.py"):
    _original = _load_source(
        "promotion_original_fleet_" + Path(_filename).stem,
        LIBRARY / "tests" / _filename,
    )
    for _name, _function in vars(_original).items():
        if _name.startswith("test_"):
            assert _name not in globals(), f"duplicate original contract test: {_name}"
            globals()[_name] = _function
