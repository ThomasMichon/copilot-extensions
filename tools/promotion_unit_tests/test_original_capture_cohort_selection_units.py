"""Original pure cohort selectors, not runner or native capture execution."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_tools = Path(__file__).resolve().parents[1]
if str(_tools) not in sys.path:
    sys.path.insert(0, str(_tools))
_path = _tools / "test_capture_cohorts.py"
_spec = importlib.util.spec_from_file_location("promotion_original_capture_cohort_selection", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)
runner_scratch = _original.runner_scratch

_SELECTED = (
    "test_capture_pair_selects_every_case_once_in_separate_observers",
    "test_capture_partition_does_not_change_other_file_selections",
    "test_capture_partition_requires_both_source_context_markers",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
