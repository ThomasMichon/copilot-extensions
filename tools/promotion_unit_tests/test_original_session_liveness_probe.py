"""Original pure probe rendering and output parsing, without probe execution."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
_library = _root / "libs" / "session-liveness-probe"
if str(_library / "src") not in sys.path:
    sys.path.insert(0, str(_library / "src"))
_path = _library / "tests" / "test_session_liveness_probe.py"
_spec = importlib.util.spec_from_file_location("promotion_original_session_liveness_probe", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_build_probe_script_contains_proc_and_lock_scan",
    "test_parse_probe_output_active_session",
    "test_parse_probe_output_idle_when_no_locks_or_processes",
    "test_parse_probe_output_process_backstop_without_lock_is_unknown",
    "test_parse_probe_output_partial_scan_without_lock_is_unknown",
    "test_parse_probe_output_nonzero_returncode_is_unknown",
    "test_parse_probe_output_invalid_header_is_unknown",
    "test_parse_probe_output_non_uuid_session_is_unknown",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
