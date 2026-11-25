"""Export original in-process source-build phase controls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_installer_source_build.py"
_spec = importlib.util.spec_from_file_location("promotion_original_source_phase_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_source_phase_allowances_are_independent_and_report_the_expired_phase",
    "test_source_phase_integrity_failure_preserves_its_original_cause",
    "test_source_progress_coalesces_output_not_completed_work",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
