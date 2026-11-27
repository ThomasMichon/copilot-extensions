"""Retain real cleanup and all six kernel-custody controls in native fixtures."""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
from types import ModuleType

import pytest

_tests = Path(__file__).resolve().parents[3] / "plugins/agent-machines/tests"
_package = ModuleType("_machines_legacy_uninstall_source")
_package.__path__ = [str(_tests)]
sys.modules[_package.__name__] = _package
_source = importlib.import_module(_package.__name__ + ".test_legacy_runtime_uninstall")
pytestmark = [
    pytest.mark.portfolio_tier("T2"), pytest.mark.effect("process"),
    pytest.mark.effect("filesystem"),
]
test_native_idle_cleanup_preserves_durable_bytes_and_is_idempotent = (
    _source.test_native_idle_cleanup_preserves_durable_bytes_and_is_idempotent
)
test_real_held_custody_refuses_concurrent_uninstall = (
    _source.test_real_held_custody_refuses_concurrent_uninstall
)
