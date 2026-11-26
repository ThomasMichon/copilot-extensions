"""Export whole original passive cleanup-diagnostic contracts."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
for _directory in ("src", "tests"):
    _source = str(_root / "libs" / "agent-procutil" / _directory)
    if _source not in sys.path:
        sys.path.insert(0, _source)
_path = _root / "libs" / "agent-procutil" / "tests" / "test_cleanup_diagnostics.py"
_spec = importlib.util.spec_from_file_location("promotion_original_cleanup_diagnostic_snapshots", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_legacy_error_retains_custody_and_snapshot_does_not_follow_retry",
    "test_public_fields_drop_non_codes_and_arbitrary_text",
    "test_native_code_is_recorded_without_rendering_or_traversing_exceptions",
    "test_resource_defined_storage_accessors_cannot_mask_custody",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
