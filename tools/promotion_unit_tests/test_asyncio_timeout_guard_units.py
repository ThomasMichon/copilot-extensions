"""Export original analysis units without replacing the native CLI cases."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_check_asyncio_timeout_handlers.py"
_spec = importlib.util.spec_from_file_location("promotion_original_asyncio_timeout_guard", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

guard = _original.guard
for _name in (
    "test_parent_maps_only_for_matching_files",
    "test_breadth_first_call_and_file_order",
    "test_invalid_source_is_still_ignored",
    "test_no_matching_calls_still_validates_path",
    "test_read_errors_still_propagate",
):
    assert _name not in globals(), f"duplicate original asyncio-timeout test: {_name}"
    globals()[_name] = getattr(_original, _name)
