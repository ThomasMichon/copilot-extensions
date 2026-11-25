"""Export original pure offline-inspection tests, not reader or guest calls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_tools = Path(__file__).resolve().parents[1]
if str(_tools) not in sys.path:
    sys.path.insert(0, str(_tools))
_path = _tools / "test_reproducer_lab_offline.py"
_spec = importlib.util.spec_from_file_location("promotion_original_reproducer_offline_inspection", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_loopback_observation_is_evidence_not_custody_or_admission",
    "test_exact_kernel_record_ceiling_and_unconfigured_ipv6_are_supported",
    "test_loopback_host_route_is_supported_but_no_external_route_is_added",
    "test_volatile_route_counters_do_not_fake_a_configuration_change",
    "test_only_actual_no_route_results_for_both_families_are_accepted",
    "test_missing_fields_changed_namespace_and_extra_claims_refuse",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
