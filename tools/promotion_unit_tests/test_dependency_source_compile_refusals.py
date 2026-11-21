"""Export original source-compile refusals without changing their witnesses."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_installer_dependency_compile.py"
_spec = importlib.util.spec_from_file_location("promotion_original_dependency_source_compile_refusals", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_compile_refuses_effective_editable_graph",
    "test_source_compile_refuses_ambient_omission_drift",
    "test_source_compile_refuses_unsafe_graph_before_uv",
    "test_source_compile_refuses_effective_disabled_source_mappings",
    "test_source_compile_refuses_context_only_local_mappings",
    "test_source_compile_refuses_inactive_local_registry_ambiguity",
)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original source-compile refusal: {_name}"
    globals()[_name] = getattr(_original, _name)
