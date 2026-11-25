"""Export original pure control-manifest refusal cases."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_promotion_control.py"
_spec = importlib.util.spec_from_file_location("promotion_original_control_manifest_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_manifest_rejects_nonportable_or_noncontrol_paths",
    "test_manifest_rejects_malformed_or_ambiguous_schema",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
