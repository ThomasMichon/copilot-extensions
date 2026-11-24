"""Run Bridge's source-only ingress controls without installing its service."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_source = Path(__file__).resolve().parents[3] / "plugins/agent-bridge/tests/test_managed_service_launcher_sh.py"
_spec = importlib.util.spec_from_file_location("_bridge_service_source", _source)
assert _spec and _spec.loader
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
pytestmark = _module.pytestmark
service_ingress = _module.service_ingress
test_posix_service_admits_selected_runtime_before_exec = _module.test_posix_service_admits_selected_runtime_before_exec
test_posix_service_refuses_before_application_python = _module.test_posix_service_refuses_before_application_python
test_posix_service_real_uv_keeps_offline_managed_runtime = _module.test_posix_service_real_uv_keeps_offline_managed_runtime
