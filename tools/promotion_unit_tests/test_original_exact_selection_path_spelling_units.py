"""Original string-only drive and UNC exact-selection refusals."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_tools = Path(__file__).resolve().parents[1]
if str(_tools) not in sys.path:
    sys.path.insert(0, str(_tools))
_path = _tools / "test_plugin_test_selection.py"
_spec = importlib.util.spec_from_file_location("promotion_original_exact_selection_path_spelling", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)
selection_fixture = _original.selection_fixture

_SELECTED = (
    "test_windows_drive_and_unc_forms_are_rejected_before_filesystem_resolution",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
