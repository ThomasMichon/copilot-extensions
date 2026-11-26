"""Export whole original in-memory hosting-result parser contracts."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
_source = str(_root / "libs" / "repository-policy" / "src")
if _source not in sys.path:
    sys.path.insert(0, _source)
_path = _root / "libs" / "repository-policy" / "tests" / "test_hosting_result.py"
_spec = importlib.util.spec_from_file_location("promotion_original_repository_hosting_result", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_complete_admission",
    "test_malformed_success_is_not_admission",
    "test_incomplete_success_is_not_admission",
    "test_non_object_is_not_admission",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
