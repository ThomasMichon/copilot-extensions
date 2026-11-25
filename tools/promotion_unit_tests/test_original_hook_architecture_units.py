"""Original hook-architecture cases using strings and fixture-only repositories."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
_path = _root / "tools" / "test_check_hook_architecture.py"
_spec = importlib.util.spec_from_file_location("promotion_original_hook_architecture", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_reports_python_starts",
    "test_ignores_native_lines",
    "test_reports_cmd_python_starts",
    "test_cmd_comment_lines_are_ignored",
    "test_hash_inside_quotes_is_not_a_comment",
    "test_scripts_outside_scripts_dir_are_scanned",
    "test_variable_prefixed_reference_is_resolved",
    "test_new_python_start_fails",
    "test_listed_python_start_passes",
    "test_stale_entry_fails",
    "test_unknown_status_is_rejected",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
