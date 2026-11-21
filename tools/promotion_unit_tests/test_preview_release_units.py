"""Export original fixture-owned preview controls without Git children."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_preview_release.py"
_spec = importlib.util.spec_from_file_location("promotion_original_preview_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

isolated = _original.isolated
_SELECTED = (
    "test_build_unknown_plugin_raises",
    "test_preview_generates_invocation_outputs_before_publication",
    "test_main_missing_plugin_reports_error",
    "test_build_refuses_a_retired_directory_pointer",
)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original preview control: {_name}"
    globals()[_name] = getattr(_original, _name)
