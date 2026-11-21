"""Export the original runtime-resolution text and temporary-file units."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_check_runtime_resolution.py"
_spec = importlib.util.spec_from_file_location("promotion_original_runtime_resolution_guard", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

for _name, _function in vars(_original).items():
    if _name.startswith("test_"):
        assert _name not in globals(), f"duplicate original runtime-resolution test: {_name}"
        globals()[_name] = _function
