"""Original injected origin callers, not installed or guest calls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_tools = Path(__file__).resolve().parents[1]
if str(_tools) not in sys.path:
    sys.path.insert(0, str(_tools))
_path = _tools / "test_reproducer_lab_origins.py"
_spec = importlib.util.spec_from_file_location("promotion_original_reproducer_origin_caller", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)
caller = _original.caller

_SELECTED = (
    "test_caller_has_fixed_finite_isolated_commands",
    "test_failed_observation_never_retries_or_returns_partial_success",
    "test_custody_loss_does_not_observe_another_runtime",
    "test_runtime_root_replacement_refuses",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
