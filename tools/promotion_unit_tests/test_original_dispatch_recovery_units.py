"""Export original deferred-promotion recovery and no-repeat decisions."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_promotion_dispatch.py"
_spec = importlib.util.spec_from_file_location("promotion_original_dispatch_recovery_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_actual_merge_completion_recovers_a_ci_advance_suppressed_while_pr_was_open",
    "test_resume_control_merge_drains_previously_paused_source_without_a_new_push",
    "test_retirement_never_automatically_retries_the_same_source",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
