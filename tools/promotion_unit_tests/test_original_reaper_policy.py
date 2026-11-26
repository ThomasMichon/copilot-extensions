"""Export whole original injected-callback reaper-policy contracts."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
_source = str(_root / "libs" / "single-instance-lease" / "src")
if _source not in sys.path:
    sys.path.insert(0, _source)
_path = _root / "libs" / "single-instance-lease" / "tests" / "test_reaper.py"
_spec = importlib.util.spec_from_file_location("promotion_original_reaper_policy", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_reaps_strays_but_spares_active_and_self",
    "test_dead_pids_are_skipped_not_terminated",
    "test_verify_gate_defeats_pid_reuse",
    "test_terminate_failure_is_fail_soft",
    "test_duplicate_and_invalid_pids_ignored",
    "test_superseded_pids_from_table_harvests_previous",
    "test_superseded_pids_from_table_includes_stale_active",
    "test_superseded_pids_from_table_no_active_pid_excludes_active",
    "test_superseded_pids_from_table_handles_junk",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
