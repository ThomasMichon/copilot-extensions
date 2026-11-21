"""Export original mocked Git and fixture-only version-bump guard controls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_check_version_bump.py"
_spec = importlib.util.spec_from_file_location("promotion_original_version_bump_guard", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

guard_module = _original.guard_module
_SELECTED = (
    "test_revision_diff_is_unambiguously_separated_from_paths",
    "test_revision_blob_reads_are_unambiguously_separated_from_paths",
    "test_diff_failure_is_not_reported_as_no_changes",
    "test_nested_shared_dependencies_charge_every_consumer_form",
    "test_nested_dependency_cycles_and_aliases_are_deterministic",
    "test_invalid_nested_manifest_fails_closed",
    "test_symlinked_nested_manifest_fails_closed",
    "test_packaged_groups_charge_standalone_consumer_for_every_canonical_root",
)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original version-bump control: {_name}"
    globals()[_name] = getattr(_original, _name)
