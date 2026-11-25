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
_spec.loader.exec_module(_module)
_original = cast(Any, _module)
engine_daemon = _original.engine_daemon
prepared_engine = _original.prepared_engine
test_real_prepared_engine_child = _original.test_real_prepared_engine_child
test_real_service_foreground_keeps_bound_uv_and_selected_environment = (
    _original.test_real_service_foreground_keeps_bound_uv_and_selected_environment
)
