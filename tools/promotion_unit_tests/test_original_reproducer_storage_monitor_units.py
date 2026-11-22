"""Export original pure storage-monitor tests, not reader or guest calls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_tools = Path(__file__).resolve().parents[1]
if str(_tools) not in sys.path:
    sys.path.insert(0, str(_tools))
_path = _tools / "test_reproducer_lab_monitoring.py"
_spec = importlib.util.spec_from_file_location("promotion_original_reproducer_storage_monitor", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_monitor_requires_measurement_again_on_resume",
    "test_exact_threshold_refuses_new_work",
    "test_slow_sample_age_cadence_lifetime_and_loss",
    "test_invalid_measurement_clears_previous_success",
    "test_policy_requires_finite_bounded_inputs",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
