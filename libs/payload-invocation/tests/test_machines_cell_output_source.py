"""Retain real Windows lifecycle-error encoding after prepared admission."""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
from types import ModuleType

_tests = Path(__file__).resolve().parents[3] / "plugins/agent-machines/tests"
_package = ModuleType("_machines_cell_output_source")
_package.__path__ = [str(_tests)]
sys.modules[_package.__name__] = _package
_source = importlib.import_module(_package.__name__ + ".test_installer_native_output_ps1")
pytestmark = _source.pytestmark
prepared_lifecycle_python = _source.prepared_lifecycle_python
test_actual_lifecycle_refusal_retains_requested_error_encoding = (
    _source.test_actual_lifecycle_refusal_retains_requested_error_encoding
)
