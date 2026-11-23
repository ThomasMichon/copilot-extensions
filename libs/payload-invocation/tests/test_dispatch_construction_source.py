"""Export original consumer controls into the lightweight native launch environment."""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
from types import ModuleType

import pytest

_root = Path(__file__).resolve().parents[3]
_tests = _root / "plugins/agent-dispatch/tests"
if not _tests.is_dir():
    pytest.skip("This integration selection requires the source consumer", allow_module_level=True)

sys.path.insert(0, str(_root / "tools"))
_package = ModuleType("_dispatch_construction_source")
_package.__path__ = [str(_tests)]
sys.modules[_package.__name__] = _package
_constructor = importlib.import_module(_package.__name__ + ".test_managed_runtime_construction_ps1")
_publication = importlib.import_module(_package.__name__ + ".test_base_python_ps1")
_snapshot = importlib.import_module(_package.__name__ + ".test_installer_engine_adoption")
_cold = importlib.import_module(_package.__name__ + ".test_managed_cold_readers_ps1")
_readiness = importlib.import_module(_package.__name__ + ".test_bootstrap_check_reconcile_always_on")
_services = importlib.import_module(_package.__name__ + ".test_managed_service_launchers_ps1")
_readers = importlib.import_module(_package.__name__ + ".test_managed_bootstrap_reader")
_source = importlib.import_module(_package.__name__ + ".test_source_runtime_install")
_descriptions = importlib.import_module(_package.__name__ + ".test_supervisor_launch_description")
_actions = importlib.import_module(_package.__name__ + ".test_action_native_ps1")
_stable = importlib.import_module(_package.__name__ + ".test_slot_service_launchers")
_custody = importlib.import_module(_package.__name__ + ".test_managed_service_custody_ps1")

prepared_reader_payload = _constructor.prepared_reader_payload
prepared_construction_python = _constructor.prepared_construction_python
prepared_managed_helper = _cold.prepared_managed_helper
service_query_probe = _custody.service_query_probe
source = _source.source
shell = _publication.shell
test_powershell_construction_uses_managed_default_without_ambient_python = (
    _constructor.test_powershell_construction_uses_managed_default_without_ambient_python
)
test_runtime_publication_gates_preference = _publication.test_runtime_publication_gates_preference
test_runtime_install_attributes_its_snapshot_not_the_calling_helper = (
    _snapshot.test_runtime_install_attributes_its_snapshot_not_the_calling_helper
)
test_powershell_cold_readers_select_managed_python_before_policy = (
    _cold.test_powershell_cold_readers_select_managed_python_before_policy
)
test_raw_powershell_cold_reader_preserves_caller_environment = (
    _cold.test_raw_powershell_cold_reader_preserves_caller_environment
)
test_powershell_current_nested_runtime_never_enters_reconciliation = (
    _readiness.test_powershell_current_nested_runtime_never_enters_reconciliation
)
test_managed_service_ingress_preserves_selected_runtime_and_policy = (
    _services.test_managed_service_ingress_preserves_selected_runtime_and_policy
)
test_managed_supervisor_description_does_not_bypass_uv = (
    _services.test_managed_supervisor_description_does_not_bypass_uv
)
test_managed_service_packaged_closure_survives_real_wheel = (
    _services.test_managed_service_packaged_closure_survives_real_wheel
)
test_managed_service_raw_snapshot_builds_closed_wheel = (
    _services.test_managed_service_raw_snapshot_builds_closed_wheel
)
test_managed_service_raw_snapshot_refuses_incomplete_closure = (
    _services.test_managed_service_raw_snapshot_refuses_incomplete_closure
)
test_managed_bootstrap_reader_preserves_bindings_and_external_root = (
    _readers.test_managed_bootstrap_reader_preserves_bindings_and_external_root
)
raw_reader_payload = _readers.raw_reader_payload
test_bootstrap_discovery_does_not_hide_invalid_runtime_choice = (
    _publication.test_bootstrap_discovery_does_not_hide_invalid_runtime_choice
)
test_signed_runtime_floor_remains_independent_of_helper_floor = (
    _publication.test_signed_runtime_floor_remains_independent_of_helper_floor
)
test_selected_base_bootstraps_source_and_construction_without_discovery = (
    _source.test_selected_base_bootstraps_source_and_construction_without_discovery
)
_windows = pytest.mark.skipif(sys.platform != "win32", reason="native Windows service entrypoints")
test_generated_description_preserves_policy_and_slot_selection = _windows(
    _descriptions.test_generated_description_preserves_policy_and_slot_selection
)
test_native_coordinator_producer_to_stop_preserves_venv_and_installation_identity = (
    _actions.test_native_coordinator_producer_to_stop_preserves_venv_and_installation_identity
)
test_stable_entrypoint_releases_file_before_slot_execution = _windows(
    _stable.test_stable_entrypoint_releases_file_before_slot_execution
)
test_managed_service_query_tree_retires_before_enclosing_job = _windows(
    _custody.test_managed_service_query_tree_retires_before_enclosing_job
)
