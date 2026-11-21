"""Export original runtime-sync controls using ordinary fixture destinations."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_sync_versioned_runtime.py"
_spec = importlib.util.spec_from_file_location("promotion_original_runtime_sync_fixture_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

repository = _original.repository
_SELECTED = (
    "test_main_repairs_ordinary_missing_and_stale_files_and_is_idempotent",
    "test_instance_only_sync_preserves_unrelated_runtime_bytes",
    "test_instance_only_sync_ships_ps_binding_and_complete_source_to_wsl_payload",
    "test_native_wheel_fanout_is_not_an_available_build_route",
    "test_lifecycle_source_archive_requires_native_accounting_dependency",
    "test_package_archive_publishes_the_same_complete_closure",
)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original runtime-sync control: {_name}"
    globals()[_name] = getattr(_original, _name)
