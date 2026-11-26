"""Expose SSH command admission without backend dependencies."""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
from types import ModuleType

_tests = Path(__file__).resolve().parents[3] / "plugins/agent-ssh/tests"
_package = ModuleType("_ssh_ready_source")
_package.__path__ = [str(_tests)]
sys.modules[_package.__name__] = _package
_source = importlib.import_module(_package.__name__ + ".test_managed_command_gate")
packaged_command = _source.packaged_command
prepared_python = _source.prepared_python
test_ready_command_admits_exact_prepared_application = (
    _source.test_ready_command_admits_exact_prepared_application
)
