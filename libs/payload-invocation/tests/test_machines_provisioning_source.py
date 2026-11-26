"""Expose retained Machines installer streaming and retry controls."""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
from types import ModuleType

_tests = Path(__file__).resolve().parents[3] / "plugins/agent-machines/tests"
_package = ModuleType("_machines_provisioning_source")
_package.__path__ = [str(_tests)]
sys.modules[_package.__name__] = _package
_source = importlib.import_module(_package.__name__ + ".test_payload_provisioning_powershell")
packaged_command = _source.packaged_command
prepared_python = _source.prepared_python
test_first_use_preserves_installer_output_and_exit_codes = (
    _source.test_first_use_preserves_installer_output_and_exit_codes
)
