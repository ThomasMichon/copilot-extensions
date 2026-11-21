"""Export original pure release-message controls without Git fixtures."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_promote_release.py"
_spec = importlib.util.spec_from_file_location("promotion_original_message_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_promote_message_lists_bumps_and_changefiles",
    "test_promote_no_changefiles_reports_none",
)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original release-message control: {_name}"
    globals()[_name] = getattr(_original, _name)
