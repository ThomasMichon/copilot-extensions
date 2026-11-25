"""Original worker controls retaining existing process and descriptor doubles."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
_path = _root / "tools" / "test_plugin_test_containment.py"
_spec = importlib.util.spec_from_file_location("promotion_original_containment_worker_doubles", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_controller_reports_status_then_stays_alive_without_leaking_control_env",
    "test_posix_worker_reaps_abandoned_group_even_if_status_write_races_owner_exit",
    "test_abandoned_controller_keeps_its_identity_through_kill_escalation",
    "test_linux_membership_ignores_unrelated_process_name_encoding",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
