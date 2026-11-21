"""Export original fixture-only packaged-asset materialization controls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_materialize_main.py"
_spec = importlib.util.spec_from_file_location("promotion_original_materializer_packaged_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_packaged_projection_assets_expand_into_own_payload",
    "test_packaged_assets_include_standalone_manager_without_plugins",
    "test_packaged_groups_project_multiple_roots_only_after_complete_preflight",
    "test_packaged_assets_retain_exact_bytes_only_when_requested",
    "test_packaged_assets_refuse_escaping_payload_directory",
    "test_packaged_assets_preserve_nested_plugin_layout_without_copying_other_plugins",
    "test_packaged_assets_refuse_unsafe_nested_file_names",
)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original packaged-asset control: {_name}"
    globals()[_name] = getattr(_original, _name)
