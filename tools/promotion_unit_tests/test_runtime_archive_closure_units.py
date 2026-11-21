"""Export original source-archive byte-closure controls without importing members."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_sync_versioned_runtime.py"
_spec = importlib.util.spec_from_file_location("promotion_original_runtime_archive_closure_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_lifecycle_source_archive_is_deterministic_and_contains_only_python",
    "test_lifecycle_source_archive_is_independent_of_zlib_implementation",
    "test_lifecycle_source_archive_is_checkout_line_ending_independent",
)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original archive-closure control: {_name}"
    globals()[_name] = getattr(_original, _name)
