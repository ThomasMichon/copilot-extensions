"""Export original promotion source-event refusal decisions."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_promotion_dispatch.py"
_spec = importlib.util.spec_from_file_location("promotion_original_dispatch_source_event_refusal_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_pending_or_failed_current_ci_cannot_wake_a_promotion",
    "test_untrusted_or_unrelated_ci_cannot_request_a_generation",
    "test_completion_must_name_the_canonical_main_promoter",
    "test_closed_pr_evidence_cannot_drift_or_hide_missing_merge_identity",
    "test_state_is_validated_before_any_dispatch",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
