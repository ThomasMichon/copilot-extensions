"""Run the original command gate controls without Dispatch application dependencies."""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
from types import ModuleType

import pytest

_tests = Path(__file__).resolve().parents[3] / "plugins/agent-dispatch/tests"
if not _tests.is_dir():
    pytest.skip("This integration selection requires the source consumer", allow_module_level=True)

_package = ModuleType("_dispatch_ready_source")
_package.__path__ = [str(_tests)]
sys.modules[_package.__name__] = _package
_source = importlib.import_module(_package.__name__ + ".test_managed_ready_ingress_ps1")
_actions = importlib.import_module(_package.__name__ + ".test_action_native_ps1")
prepared_managed_helper = _source.prepared_managed_helper
ready_application_python = _source.ready_application_python
ready_command_payload = _source.ready_command_payload
test_ready_command_admits_managed_application_without_acquisition = (
    _source.test_ready_command_admits_managed_application_without_acquisition
)
test_ready_runtime_executes_before_any_action_policy_prerequisite = (
    _actions.test_ready_runtime_executes_before_any_action_policy_prerequisite
)
test_full_global_entry_keeps_default_bootstrap_veto_and_warm_availability = (
    _actions.test_full_global_entry_keeps_default_bootstrap_veto_and_warm_availability
)
