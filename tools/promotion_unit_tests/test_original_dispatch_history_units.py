"""Export original admission and retirement-history decision controls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_promotion_dispatch.py"
_spec = importlib.util.spec_from_file_location("promotion_original_dispatch_history_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_admission_binds_the_actual_gate_attempt_and_reuses_it_for_job_only_recovery",
    "test_admission_reads_all_job_attempt_pages_before_deciding",
    "test_retirement_paginates_closed_history_and_binds_candidate_bytes",
    "test_retirement_record_readback_mismatch_is_an_error_not_permission_to_dispatch",
    "test_retirement_inventory_is_bounded_and_never_accepts_a_truncated_prefix",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
