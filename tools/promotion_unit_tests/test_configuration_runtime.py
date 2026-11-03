"""Admit only actor-free resolver contracts; real shell controls stay opt-in."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "promotion_configuration_runtime_cases",
    ROOT / "libs/configuration-layers/tests/test_configuration_runtime.py",
)
assert SPEC and SPEC.loader
cases = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cases
SPEC.loader.exec_module(cases)

test_absent_resolver_never_starts_anything = cases.test_absent_resolver_never_starts_anything
test_undeclared_read_only_resolver_fails_before_process = cases.test_undeclared_read_only_resolver_fails_before_process
test_resolver_output_is_bounded_and_stays_in_qualified_slots = cases.test_resolver_output_is_bounded_and_stays_in_qualified_slots
test_real_resolver_failure_is_not_optional_absence = cases.test_real_resolver_failure_is_not_optional_absence
test_native_command_has_explicit_read_only_routing = cases.test_native_command_has_explicit_read_only_routing
