"""Run the original inert engine controls without Index's application dependencies."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any, cast

_source = (
    Path(__file__).resolve().parents[3]
    / "plugins/agent-index/tests/test_managed_engine_launch.py"
)
_spec = importlib.util.spec_from_file_location("_index_engine_launch_source", _source)
assert _spec and _spec.loader
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
sys.path.insert(0, str(_source.parent))
try:
    _spec.loader.exec_module(_module)
finally:
    sys.path.pop(0)
_original = cast(Any, _module)
engine_daemon = _original.engine_daemon
prepared_engine = _original.prepared_engine
test_real_prepared_engine_child = _original.test_real_prepared_engine_child
test_real_prepared_indexing_worker = _original.test_real_prepared_indexing_worker
test_real_service_foreground_keeps_bound_uv_and_selected_environment = (
    _original.test_real_service_foreground_keeps_bound_uv_and_selected_environment
)
test_native_engine_management_retains_admitted_uv = (
    _original.test_native_engine_management_retains_admitted_uv
)

_worker_source = _source.with_name("test_worker_runtime_admission.py")
_worker_spec = importlib.util.spec_from_file_location("_index_worker_admission_source", _worker_source)
assert _worker_spec and _worker_spec.loader
_worker_module = importlib.util.module_from_spec(_worker_spec)
sys.modules[_worker_spec.name] = _worker_module
sys.path.insert(0, str(_source.parents[1] / "src"))
try:
    _worker_spec.loader.exec_module(_worker_module)
finally:
    sys.path.pop(0)
_worker = cast(Any, _worker_module)
test_prepared_worker_refuses_before_log_or_spawn = (
    _worker.test_prepared_worker_refuses_before_log_or_spawn
)
test_admission_refusal_settles_without_worker_or_retry = (
    _worker.test_admission_refusal_settles_without_worker_or_retry
)
