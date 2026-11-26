"""Expose MCP native command controls without unrelated application dependencies."""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
from types import ModuleType

_tests = Path(__file__).resolve().parents[3] / "plugins/agent-mcp/tests"
_package = ModuleType("_mcp_ready_source")
_package.__path__ = [str(_tests)]
sys.modules[_package.__name__] = _package
_source = importlib.import_module(_package.__name__ + ".test_managed_command_gate")
packaged_hooks = _source.packaged_hooks
prepared_helper = _source.prepared_helper
application_python = _source.application_python
test_ready_command_admits_exact_prepared_application = (
    _source.test_ready_command_admits_exact_prepared_application
)
