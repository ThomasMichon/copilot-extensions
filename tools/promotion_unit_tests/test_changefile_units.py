"""Export original serial, fixture-owned changefile controls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_changefile.py"
_spec = importlib.util.spec_from_file_location("promotion_original_changefile_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_isolated_changefiles_dir = _original._isolated_changefiles_dir
_SELECTED = (
    "test_write_and_read_changefile",
    "test_read_changefiles_empty_dir_returns_empty",
    "test_read_changefiles_skips_unreadable_file",
    "test_cmd_add_writes_multiple_pairs",
    "test_cmd_add_rejects_mismatched_pairs",
    "test_cmd_add_rejects_invalid_type",
    "test_cmd_list_reports_pending",
)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original changefile control: {_name}"
    globals()[_name] = getattr(_original, _name)
