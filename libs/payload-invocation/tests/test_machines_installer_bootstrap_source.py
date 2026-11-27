"""Expose native Machines setup seed controls without application imports."""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
from types import ModuleType

_tests = Path(__file__).resolve().parents[3] / "plugins/agent-machines/tests"
_package = ModuleType("_machines_installer_bootstrap_source")
_package.__path__ = [str(_tests)]
sys.modules[_package.__name__] = _package
_source = importlib.import_module(_package.__name__ + ".test_installer_bootstrap")
_index = importlib.import_module(_package.__name__ + ".test_installer_index")
pytestmark = _source.pytestmark
installer_python = _source.installer_python
lifecycle = _source.lifecycle
bootstrap = _source.bootstrap
installer = _source.installer
test_inspect_reuses_managed_bootstrap_without_acquisition = (
    _source.test_inspect_reuses_managed_bootstrap_without_acquisition
)
test_inspect_refuses_without_fallback_or_acquisition = (
    _source.test_inspect_refuses_without_fallback_or_acquisition
)
test_prepare_reaches_declared_managed_python_without_ambient_interpreter = (
    _source.test_prepare_reaches_declared_managed_python_without_ambient_interpreter
)
test_prepare_and_inspect_discover_uv_at_configured_destination = (
    _source.test_prepare_and_inspect_discover_uv_at_configured_destination
)
test_actual_installer_selects_before_source_identity = (
    _source.test_actual_installer_selects_before_source_identity
)
test_cell_watchdog_uses_selected_python_before_any_source_helper = (
    _source.test_cell_watchdog_uses_selected_python_before_any_source_helper
)
test_invalid_cell_request_never_prepares_prerequisites = (
    _source.test_invalid_cell_request_never_prepares_prerequisites
)
test_native_stamp_binstub_does_not_select_installer_python = (
    _source.test_native_stamp_binstub_does_not_select_installer_python
)
test_bootstrap_forwarding_preserves_watchdog_deadline_mode = (
    _source.test_bootstrap_forwarding_preserves_watchdog_deadline_mode
)
test_application_constructor_uses_independent_managed_requirement = (
    _source.test_application_constructor_uses_independent_managed_requirement
)
test_native_index_adapter_retains_user_and_site_precedence = (
    _index.test_native_index_adapter_retains_user_and_site_precedence
)
