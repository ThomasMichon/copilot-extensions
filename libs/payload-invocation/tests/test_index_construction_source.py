"""Export Index's actual constructor controls without installing its application."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any, cast

_source = (
    Path(__file__).resolve().parents[3]
    / "plugins/agent-index/tests/test_managed_runtime_construction_ps1.py"
)
_spec = importlib.util.spec_from_file_location("_index_construction_source", _source)
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
test_native_construction_retains_managed_selection_and_slot_custody = (
    _original.test_native_construction_retains_managed_selection_and_slot_custody
)
