"""Expose the canonical capture's modeled and caller-visible diagnostic controls."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_path = (
    Path(__file__).resolve().parents[3]
    / "libs/agent-procutil/tests/test_native_capture_settlement.py"
)
_spec = importlib.util.spec_from_file_location("_native_capture_settlement_source", _path)
assert _spec and _spec.loader
_source = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_source)
test_settlement_diagnostic_retains_failure_and_original_observation = (
    _source.test_settlement_diagnostic_retains_failure_and_original_observation
)
test_capture_caller_emits_settlement_reason_after_real_cleanup = (
    _source.test_capture_caller_emits_settlement_reason_after_real_cleanup
)
