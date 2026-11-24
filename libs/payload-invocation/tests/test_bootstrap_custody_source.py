"""Retain the original native custody controls without installing the Bridge service."""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
from types import ModuleType

import pytest

_root = Path(__file__).resolve().parents[3]
_tests = _root / "plugins/agent-bridge/tests"
if not _tests.is_dir():
    pytest.skip("This integration selection requires the source consumer", allow_module_level=True)

sys.path.insert(0, str(_tests))
_package = ModuleType("_bridge_bootstrap_custody_source")
_package.__path__ = [str(_tests)]
sys.modules[_package.__name__] = _package
_source = importlib.import_module(_package.__name__ + ".test_bootstrap_capture_native")

pytestmark = _source.pytestmark
windows_payload_runtime = _source.windows_payload_runtime
selector_probe = _source.selector_probe
test_unhinted_selector_caller_death_retires_descendants = (
    _source.test_unhinted_selector_caller_death_retires_descendants
)
test_unowned_capture_control_survives_caller_death = (
    _source.test_unowned_capture_control_survives_caller_death
)
test_completed_capture_does_not_pin_asset_or_reload_retirement = (
    _source.test_completed_capture_does_not_pin_asset_or_reload_retirement
)
test_selector_timeout_retires_descendants_while_caller_stays_alive = (
    _source.test_selector_timeout_retires_descendants_while_caller_stays_alive
)
