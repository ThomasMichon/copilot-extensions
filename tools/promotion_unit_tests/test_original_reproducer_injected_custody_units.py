"""Export original injected operation custody controls, not real guest state."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_tools = Path(__file__).resolve().parents[1]
if str(_tools) not in sys.path:
    sys.path.insert(0, str(_tools))
_path = _tools / "test_reproducer_lab_operation.py"
_spec = importlib.util.spec_from_file_location("promotion_original_reproducer_injected_custody", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)
operation_seams = _original.operation_seams

_SELECTED = (
    "test_operation_checks_before_yield_each_effect_and_exit",
    "test_same_guest_selection_cannot_enter_twice",
    "test_failed_operation_never_replays_or_retains_its_lock",
    "test_state_replacement_at_exit_refuses_and_releases",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
