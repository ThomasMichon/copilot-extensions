"""Export the original Picker inbox AST and temporary-repository units."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_check_picker_inbox_discipline.py"
_spec = importlib.util.spec_from_file_location("promotion_original_picker_inbox_guard", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)
repo = _original.repo

for _name, _function in vars(_original).items():
    if _name.startswith("test_"):
        assert _name not in globals(), f"duplicate original Picker inbox test: {_name}"
        globals()[_name] = _function
