"""Expose real finite Machines removal controls without application imports."""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
from types import ModuleType

_tests = Path(__file__).resolve().parents[3] / "plugins/agent-machines/tests"
_package = ModuleType("_machines_uninstall_bootstrap_source")
_package.__path__ = [str(_tests)]
sys.modules[_package.__name__] = _package
_source = importlib.import_module(_package.__name__ + ".test_uninstall_bootstrap")
pytestmark = _source.pytestmark
installer_python = _source.installer_python
lifecycle = _source.lifecycle
bootstrap = _source.bootstrap
installer = _source.installer
removal = _source.removal
test_uninstall_reuses_prepared_python_and_preserves_durable_state = (
    _source.test_uninstall_reuses_prepared_python_and_preserves_durable_state
)
test_uninstall_missing_uv_refuses_before_removal_without_ambient_fallback = (
    _source.test_uninstall_missing_uv_refuses_before_removal_without_ambient_fallback
)
test_uninstall_retains_live_generation_lease_refusal = (
    _source.test_uninstall_retains_live_generation_lease_refusal
)
test_uninstall_refuses_unavailable_process_observation = (
    _source.test_uninstall_refuses_unavailable_process_observation
)
test_uninstall_rejects_selected_python_within_removal_root = (
    _source.test_uninstall_rejects_selected_python_within_removal_root
)
