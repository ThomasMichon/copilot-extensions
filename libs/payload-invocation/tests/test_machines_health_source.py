"""Expose the retained Machines capability and rollback boundary."""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
from types import ModuleType

_tests = Path(__file__).resolve().parents[3] / "plugins/agent-machines/tests"
_package = ModuleType("_machines_health_source")
_package.__path__ = [str(_tests)]
sys.modules[_package.__name__] = _package
_source = importlib.import_module(_package.__name__ + ".test_health_job_payload")
packaged_command = _source.packaged_command
prepared_python = _source.prepared_python
test_fresh_payload_selection_checks_capability_before_new_syntax_and_rechecks_rollback = (
    _source.test_fresh_payload_selection_checks_capability_before_new_syntax_and_rechecks_rollback
)
