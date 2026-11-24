"""Run Bridge's source-only Windows service controls without installing Bridge."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any, cast

_source = (
    Path(__file__).resolve().parents[3]
    / "plugins/agent-bridge/tests/test_managed_service_launcher_ps1.py"
)
_spec = importlib.util.spec_from_file_location("_bridge_windows_service_source", _source)
assert _spec and _spec.loader
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
_spec.loader.exec_module(_module)
_original = cast(Any, _module)
pytestmark = _original.pytestmark
windows_service = _original.windows_service
service_shell = _original.service_shell
test_windows_service_updates_existing_task_launcher_idempotently = (
    _original.test_windows_service_updates_existing_task_launcher_idempotently
)
test_windows_service_real_uv_keeps_offline_selected_worker = (
    _original.test_windows_service_real_uv_keeps_offline_selected_worker
)
test_windows_service_readonly_and_healthy_pid_need_no_uv = (
    _original.test_windows_service_readonly_and_healthy_pid_need_no_uv
)
test_windows_service_missing_prerequisites_refuse_before_python = (
    _original.test_windows_service_missing_prerequisites_refuse_before_python
)
test_service_publisher_refreshes_script_without_task_mutation = (
    _original.test_service_publisher_refreshes_script_without_task_mutation
)

_task_source = _source.with_name("test_install_ps1_s4u_recovery.py")
_task_spec = importlib.util.spec_from_file_location("_bridge_windows_task_source", _task_source)
assert _task_spec and _task_spec.loader
_tasks = importlib.util.module_from_spec(_task_spec)
sys.modules[_task_spec.name] = _tasks
_task_spec.loader.exec_module(_tasks)
_task_controls = cast(Any, _tasks)
test_scheduling_policy_is_task_only_and_repeatable = (
    _task_controls.test_scheduling_policy_is_task_only_and_repeatable
)
test_scheduling_policy_distinguishes_missing_from_unknown = (
    _task_controls.test_scheduling_policy_distinguishes_missing_from_unknown
)
test_native_reconcile_fast_paths_apply_scheduling_policy = (
    _task_controls.test_native_reconcile_fast_paths_apply_scheduling_policy
)
test_task_repair_restores_interactive_mode_without_changing_normal_provision = (
    _task_controls.test_task_repair_restores_interactive_mode_without_changing_normal_provision
)
