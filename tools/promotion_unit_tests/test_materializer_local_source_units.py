"""Export original fixture-only local-source closure controls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_materialize_main.py"
_spec = importlib.util.spec_from_file_location("promotion_original_materializer_local_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_materialize_refuses_incomplete_already_local_sources",
    "test_materialize_refuses_incomplete_nested_local_sources",
    "test_materialize_accepts_complete_local_sibling_closure",
    "test_materialize_requires_existing_standalone_manifest_but_allows_skill_plugins",
    "test_materialize_refuses_direct_local_requirements",
    "test_materialize_declared_flat_python_modules",
    "test_retained_flat_reference_requires_explicit_boolean_false",
    "test_materialize_refuses_undeclared_or_invalid_flat_sources",
)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original local-source control: {_name}"
    globals()[_name] = getattr(_original, _name)
