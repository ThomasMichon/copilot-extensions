"""Export original Index bootstrap controls without installing its application."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any, cast

_source = (
    Path(__file__).resolve().parents[3]
    / "plugins/agent-index/tests/test_managed_bootstrap_reader_ps1.py"
)
_spec = importlib.util.spec_from_file_location("_index_bootstrap_source", _source)
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
test_bootstrap_readers_inspect_bound_managed_python = (
    _original.test_bootstrap_readers_inspect_bound_managed_python
)
test_explicit_closed_wheel_python_keeps_its_admission_owner = (
    _original.test_explicit_closed_wheel_python_keeps_its_admission_owner
)
test_actual_entry_selects_before_runtime = _original.test_actual_entry_selects_before_runtime
