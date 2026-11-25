"""Run the canonical shell provider controls in the shared native test environment."""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
from types import ModuleType

_root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_root / "tools"))
_package = ModuleType("_uv_selection_source")
_package.__path__ = [str(_root / "tools")]
sys.modules[_package.__name__] = _package
_provider = importlib.import_module(_package.__name__ + ".test_uv_bootstrap_provider")

host = _provider.host
test_posix_runtime_selection_preserves_the_completed_environment = (
    _provider.test_posix_runtime_selection_preserves_the_completed_environment
)
test_posix_bootstrap_is_managed_only_and_acquires_only_for_setup = (
    _provider.test_posix_bootstrap_is_managed_only_and_acquires_only_for_setup
)
test_posix_bootstrap_rejects_malformed_requests_before_uv = (
    _provider.test_posix_bootstrap_rejects_malformed_requests_before_uv
)
test_real_posix_lookup_ignores_config_without_changing_caller = (
    _provider.test_real_posix_lookup_ignores_config_without_changing_caller
)
test_provider_is_materialized_only_to_declared_native_consumer = (
    _provider.test_provider_is_materialized_only_to_declared_native_consumer
)
test_native_runtime_selection_is_exact_prepared_only_and_isolated = (
    _provider.test_native_runtime_selection_is_exact_prepared_only_and_isolated
)
test_native_runtime_selection_honors_explicit_uv_without_discovery = (
    _provider.test_native_runtime_selection_honors_explicit_uv_without_discovery
)
test_native_runtime_selection_rejects_bad_explicit_uv_without_fallback = (
    _provider.test_native_runtime_selection_rejects_bad_explicit_uv_without_fallback
)
test_native_runtime_selection_refuses_without_fallback = (
    _provider.test_native_runtime_selection_refuses_without_fallback
)
test_native_runtime_selection_refuses_missing_prerequisites_before_capture = (
    _provider.test_native_runtime_selection_refuses_missing_prerequisites_before_capture
)
test_native_runtime_selection_uses_real_uv_and_capture_without_path_python = (
    _provider.test_native_runtime_selection_uses_real_uv_and_capture_without_path_python
)
test_managed_selection_preserves_exact_request_and_operation = (
    _provider.test_managed_selection_preserves_exact_request_and_operation
)
test_unprepared_lookup_never_acquires = _provider.test_unprepared_lookup_never_acquires
test_missing_uv_does_not_discover_python = _provider.test_missing_uv_does_not_discover_python
test_legacy_capture_contract_remains_usable_without_selection_isolation = (
    _provider.test_legacy_capture_contract_remains_usable_without_selection_isolation
)
test_selection_isolation_refuses_an_older_callback_before_execution = (
    _provider.test_selection_isolation_refuses_an_older_callback_before_execution
)
test_invalid_declaration_refuses_before_uv = _provider.test_invalid_declaration_refuses_before_uv
test_unqualified_uv_or_unconfirmed_custody_stops = (
    _provider.test_unqualified_uv_or_unconfirmed_custody_stops
)
test_policy_refusal_does_not_retry_or_fall_back = _provider.test_policy_refusal_does_not_retry_or_fall_back
test_failed_or_malformed_lookup_is_not_a_missing_interpreter = (
    _provider.test_failed_or_malformed_lookup_is_not_a_missing_interpreter
)
test_real_uv_lookup_uses_production_capture_without_path_python = (
    _provider.test_real_uv_lookup_uses_production_capture_without_path_python
)
