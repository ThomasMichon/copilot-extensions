"""Run native unit-ownership controls without installing runtime plugins."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any, cast

_source = Path(__file__).resolve().parents[3] / "tools/test_installer_engine_stop.py"
_spec = importlib.util.spec_from_file_location("_service_unit_source", _source)
assert _spec and _spec.loader
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
_spec.loader.exec_module(_module)
_original = cast(Any, _module)
test_actual_unit_writer_is_accepted_by_daemon_admission = _original.test_actual_unit_writer_is_accepted_by_daemon_admission
test_real_uninstall_preflight_binds_generated_unit_not_comment = _original.test_real_uninstall_preflight_binds_generated_unit_not_comment
test_dispatch_supervisor_registration_cannot_name_another_unit = _original.test_dispatch_supervisor_registration_cannot_name_another_unit
test_dispatch_historical_coordinator_registration_remains_owned = _original.test_dispatch_historical_coordinator_registration_remains_owned
test_real_preflight_checks_manager_effective_service_ownership = _original.test_real_preflight_checks_manager_effective_service_ownership
