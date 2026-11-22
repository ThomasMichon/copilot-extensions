"""Export original optional H1 validator malformed-shape controls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_capture_bridge_host_inspection.py"
_spec = importlib.util.spec_from_file_location("promotion_original_h1_optional_shape_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = ("test_h1_optional_validator_defers_malformed_shape_to_registry_owner",)
globals().update({name: getattr(_original, name) for name in _SELECTED})
