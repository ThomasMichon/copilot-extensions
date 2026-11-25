"""Export original admission and retirement evidence-state decisions."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_promotion_dispatch.py"
_spec = importlib.util.spec_from_file_location("promotion_original_dispatch_evidence_states_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_successful_recorded_gate_distinguishes_admission_from_waiting_or_paused_noops",
    "test_unavailable_admission_evidence_refuses_instead_of_guessing",
    "test_retirement_connection_cannot_hide_incomplete_or_malformed_history",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
