"""Export original ordinary/simulated UV-reference controls; real links stay out."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_uv_editable_ref.py"
_spec = importlib.util.spec_from_file_location("promotion_original_uv_reference_fixture_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_symlink_scan_falls_back_without_native_symlink_support",
    "test_uv_editable_relpath_is_always_forward_slashed",
    "test_uv_editable_relpath_matches_plain_relpath_on_posix",
    "test_rewrite_scoped_to_uv_sources_table_only",
    "test_can_rewrite_uv_source_to_editable_true_when_present",
    "test_can_rewrite_uv_source_to_editable_false_when_absent",
    "test_can_rewrite_uv_source_to_editable_ignores_a_match_outside_the_table",
    "test_lib_tree_matches_true_for_identical_trees",
    "test_lib_tree_matches_false_when_any_piece_differs",
    "test_lib_tree_matches_false_when_only_copy_has_tests",
    "test_lib_tree_matches_ignores_only_relative_build_dir_names",
    "test_lib_tree_matches_ignores_egg_info_artifacts",
    "test_lib_tree_matches_ignores_dot_venv_artifacts",
    "test_find_uv_editable_refs_editable_requires_exact_true",
    "test_is_safe_lib_name",
    "test_convert_to_uv_editable_refuses_an_unsafe_lib_name",
    "test_find_uv_editable_refs_raises_manifest_unreadable_for_malformed_toml",
    "test_find_uv_editable_refs_returns_empty_for_a_genuinely_absent_manifest",
    "test_uv_editable_problems_surfaces_a_malformed_manifest",
    "test_uv_editable_problems_rejects_an_unsafe_lib_name",
    "test_find_uv_editable_refs_raises_manifest_unreadable_for_a_non_string_path",
    "test_lib_tree_matches_false_for_an_unexpected_extra_file",
    "test_find_uv_editable_refs_raises_manifest_unreadable_for_a_non_table_sources",
    "test_find_uv_editable_refs_raises_manifest_unreadable_for_invalid_utf8",
    "test_uv_editable_problems_rejects_a_canonical_lib_missing_src",
    "test_uv_editable_problems_rejects_a_canonical_lib_missing_pyproject",
    "test_convert_to_uv_editable_uses_the_injected_libs_dir_for_the_relpath",
)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original UV-reference control: {_name}"
    globals()[_name] = getattr(_original, _name)
