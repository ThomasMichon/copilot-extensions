"""Expose the original documentation navigation units without copying them."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_specification = importlib.util.spec_from_file_location(
    "promotion_original_docs_consistency", ROOT / "tools/test_check_docs_consistency.py"
)
assert _specification and _specification.loader
_original = importlib.util.module_from_spec(_specification)
sys.modules[_specification.name] = _original
_specification.loader.exec_module(_original)
for _name, _function in vars(_original).items():
    if _name.startswith("test_"):
        assert _name not in globals(), f"duplicate original documentation test: {_name}"
        globals()[_name] = _function
