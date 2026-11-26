"""Expose native Index command controls without application dependencies."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any, cast

_source = Path(__file__).resolve().parents[3] / "plugins/agent-index/tests/test_managed_command_gate.py"
_spec = importlib.util.spec_from_file_location("_index_command_gate_source", _source)
assert _spec and _spec.loader
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
sys.path.insert(0, str(_source.parent))
try:
    _spec.loader.exec_module(_module)
finally:
    sys.path.pop(0)
_original = cast(Any, _module)
prepared_helper = _original.prepared_helper
application_python = _original.application_python
test_command_gate_retains_prepared_bindings_without_ambient_fallback = (
    _original.test_command_gate_retains_prepared_bindings_without_ambient_fallback
)
