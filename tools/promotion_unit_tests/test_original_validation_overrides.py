"""Export original source-level validation merge/parser controls unchanged."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_validation_config_overrides.py"
_spec = importlib.util.spec_from_file_location("promotion_original_validation_overrides", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = tuple(_name for _name in vars(_original) if _name.startswith("test_"))
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original validation override control: {_name}"
    globals()[_name] = getattr(_original, _name)
