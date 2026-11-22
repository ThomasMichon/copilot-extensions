"""Export original subject-verifier retry controls without replacing witnesses."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_attestation_verifier_retry.py"
_spec = importlib.util.spec_from_file_location("promotion_original_attestation_verifier_retry", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

subject = _original.subject
_SELECTED = (
    "test_init_failure_retries_identical_policy_with_remaining_deadline",
    "test_other_failures_never_retry",
    "test_second_failure_is_fatal_without_third_attempt",
    "test_exhausted_original_deadline_never_starts_retry",
    "test_success_keeps_original_single_call",
    "test_subprocess_timeout_is_not_retried_or_converted_to_success",
)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original subject-verifier retry control: {_name}"
    globals()[_name] = getattr(_original, _name)
