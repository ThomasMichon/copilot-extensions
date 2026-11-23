"""Export original consumer controls into the lightweight native launch environment."""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
from types import ModuleType

import pytest

_root = Path(__file__).resolve().parents[3]
_tests = _root / "plugins/agent-dispatch/tests"
if not _tests.is_dir():
    pytest.skip("This integration selection requires the source consumer", allow_module_level=True)

sys.path.insert(0, str(_root / "tools"))
_package = ModuleType("_dispatch_construction_source")
_package.__path__ = [str(_tests)]
sys.modules[_package.__name__] = _package
_constructor = importlib.import_module(_package.__name__ + ".test_managed_runtime_construction_ps1")
_publication = importlib.import_module(_package.__name__ + ".test_base_python_ps1")
_snapshot = importlib.import_module(_package.__name__ + ".test_installer_engine_adoption")
_cold = importlib.import_module(_package.__name__ + ".test_managed_cold_readers_ps1")

prepared_reader_payload = _constructor.prepared_reader_payload
prepared_construction_python = _constructor.prepared_construction_python
prepared_managed_helper = _cold.prepared_managed_helper
shell = _publication.shell
test_powershell_construction_uses_managed_default_without_ambient_python = (
    _constructor.test_powershell_construction_uses_managed_default_without_ambient_python
)
test_runtime_publication_gates_preference = _publication.test_runtime_publication_gates_preference
test_runtime_install_attributes_its_snapshot_not_the_calling_helper = (
    _snapshot.test_runtime_install_attributes_its_snapshot_not_the_calling_helper
)
test_powershell_cold_readers_select_managed_python_before_policy = (
    _cold.test_powershell_cold_readers_select_managed_python_before_policy
)
test_raw_powershell_cold_reader_preserves_caller_environment = (
    _cold.test_raw_powershell_cold_reader_preserves_caller_environment
)
