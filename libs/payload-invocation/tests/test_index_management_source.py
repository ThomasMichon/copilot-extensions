"""Export native management controls without installing Index's application."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any, cast

_source = (
    Path(__file__).resolve().parents[3]
    / "plugins/agent-index/tests/test_managed_management_ps1.py"
)
_spec = importlib.util.spec_from_file_location("_index_management_source", _source)
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
test_management_admits_only_prepared_completed_runtime = (
    _original.test_management_admits_only_prepared_completed_runtime
)
test_activation_role_requires_prepared_bootstrap_without_ambient_fallback = (
    _original.test_activation_role_requires_prepared_bootstrap_without_ambient_fallback
)
