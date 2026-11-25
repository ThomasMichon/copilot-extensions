"""Export the original mocked module-size repository probe timeout control."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_check_module_size.py"
_spec = importlib.util.spec_from_file_location("promotion_original_module_size_probe", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = ("test_repository_probe_timeout_is_a_controlled_failure",)
for _name in _SELECTED:
    globals()[_name] = getattr(_original, _name)
