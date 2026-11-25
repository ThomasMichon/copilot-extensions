"""Export POSIX management controls without Index's application dependencies."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any, cast

_source = (
    Path(__file__).resolve().parents[3]
    / "plugins/agent-index/tests/test_managed_management_sh.py"
)
_spec = importlib.util.spec_from_file_location("_index_management_sh_source", _source)
assert _spec and _spec.loader
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
sys.path.insert(0, str(_source.parent))
try:
    _spec.loader.exec_module(_module)
finally:
    sys.path.pop(0)
_original = cast(Any, _module)
pytestmark = _original.pytestmark
prepared_helper = _original.prepared_helper
test_management_admits_selected_runtime_before_effects = (
    _original.test_management_admits_selected_runtime_before_effects
)
test_management_refusal_stops_dependent_effects = (
    _original.test_management_refusal_stops_dependent_effects
)
