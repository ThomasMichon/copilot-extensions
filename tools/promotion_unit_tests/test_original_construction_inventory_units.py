"""Export the unchanged original fixture-based construction-inventory class."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_construction_inventory.py"
_spec = importlib.util.spec_from_file_location("promotion_original_construction_inventory", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

ConstructionInventoryTests = _original.ConstructionInventoryTests
