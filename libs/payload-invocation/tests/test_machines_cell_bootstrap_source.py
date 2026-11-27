"""Expose inert Machines installer-ingress controls without application imports."""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
from types import ModuleType

_tests = Path(__file__).resolve().parents[3] / "plugins/agent-machines/tests"
_package = ModuleType("_machines_cell_bootstrap_source")
_package.__path__ = [str(_tests)]
sys.modules[_package.__name__] = _package
_source = importlib.import_module(_package.__name__ + ".test_cell_lifecycle_bootstrap")
pytestmark = _source.pytestmark
installer_python = _source.installer_python
lifecycle = _source.lifecycle
test_cell_lifecycle_uses_prepared_installer_python = (
    _source.test_cell_lifecycle_uses_prepared_installer_python
)
test_cell_lifecycle_missing_prerequisites_never_falls_back = (
    _source.test_cell_lifecycle_missing_prerequisites_never_falls_back
)
