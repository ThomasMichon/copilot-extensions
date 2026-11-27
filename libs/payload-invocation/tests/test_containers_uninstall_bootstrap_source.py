"""Expose prepared-only Containers removal without application imports."""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
from types import ModuleType

_tests = Path(__file__).resolve().parents[3] / "plugins/agent-containers/tests"
_package = ModuleType("_containers_uninstall_bootstrap_source")
_package.__path__ = [str(_tests)]
sys.modules[_package.__name__] = _package
_source = importlib.import_module(_package.__name__ + ".test_uninstall_bootstrap")
pytestmark = _source.pytestmark
removal = _source.removal
test_uninstall_reuses_prepared_python_without_ambient_fallback = (
    _source.test_uninstall_reuses_prepared_python_without_ambient_fallback
)
test_missing_uv_refuses_before_helper_or_transaction = (
    _source.test_missing_uv_refuses_before_helper_or_transaction
)
test_unavailable_process_observation_refuses_without_removing_bytes = (
    _source.test_unavailable_process_observation_refuses_without_removing_bytes
)
