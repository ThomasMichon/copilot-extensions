"""Retain real cleanup, kernel custody and actual Containers transport refusals."""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
from types import ModuleType

import pytest

_tests = Path(__file__).resolve().parents[3] / "plugins/agent-containers/tests"
_package = ModuleType("_containers_legacy_uninstall_source")
_package.__path__ = [str(_tests)]
sys.modules[_package.__name__] = _package
_source = importlib.import_module(_package.__name__ + ".test_containers_legacy_runtime_uninstall")
pytestmark = [
    pytest.mark.portfolio_tier("T2"), pytest.mark.effect("process"),
    pytest.mark.effect("filesystem"),
]
rooted_broker = _source.rooted_broker
test_native_idle_cleanup_preserves_durable_bytes_and_is_idempotent = (
    _source.test_native_idle_cleanup_preserves_durable_bytes_and_is_idempotent
)
test_real_held_custody_refuses_concurrent_uninstall = (
    _source.test_real_held_custody_refuses_concurrent_uninstall
)
test_native_reconnect_closure_refusal_retains_all_transport_bytes = (
    _source.test_native_reconnect_closure_refusal_retains_all_transport_bytes
)
test_native_uninstall_refuses_actual_live_loopback_broker = (
    _source.test_native_uninstall_refuses_actual_live_loopback_broker
)
test_transport_guard_reports_actual_runtime_rooted_broker_without_stopping_it = (
    _source.test_transport_guard_reports_actual_runtime_rooted_broker_without_stopping_it
)
test_transport_guard_distinguishes_stale_artifacts_from_busy = (
    _source.test_transport_guard_distinguishes_stale_artifacts_from_busy
)
