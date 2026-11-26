"""Export whole original explicit-clock startup contracts."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
_source = str(_root / "libs" / "agent-procutil" / "src")
if _source not in sys.path:
    sys.path.insert(0, _source)
_path = _root / "libs" / "agent-procutil" / "tests" / "test_startup.py"
_spec = importlib.util.spec_from_file_location("promotion_original_procutil_startup_clocks", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_selected_startup_policy_is_not_a_turn_budget",
    "test_every_received_update_renews_only_contact",
    "test_local_timer_and_remote_timestamp_do_not_renew_contact",
    "test_policy_rejects_invalid_budgets",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
