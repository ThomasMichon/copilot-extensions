"""Export original fixture-only UV-reference controls without native actors."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_materialize_main.py"
_spec = importlib.util.spec_from_file_location("promotion_original_materializer_uv_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_find_uv_editable_refs_only_returns_escaping_entries_regardless_of_editable",
    "test_find_uv_editable_refs_includes_a_non_editable_escaping_entry",
    "test_materialize_uv_editable_ref_into_copies_full_tree_and_rewrites_entry",
    "test_materialize_uv_editable_ref_into_skips_missing_canonical",
    "test_materialize_uv_editable_ref_into_refuses_a_non_editable_entry",
    "test_materialize_uv_editable_ref_into_refuses_escaping_canonical_root",
    "test_materialize_uv_editable_ref_into_refuses_a_path_outside_the_libs_tree",
    "test_materialize_uv_editable_refs_whole_repo",
    "test_materialize_whole_repo_also_expands_uv_editable_refs",
    "test_materialize_uv_editable_ref_into_preflights_rewrite_before_copying",
    "test_materialize_uv_editable_ref_into_refuses_a_malformed_canonical_lib",
    "test_materialize_uv_editable_ref_into_refuses_a_canonical_lib_missing_pyproject",
    "test_materialize_uv_editable_ref_into_refuses_an_unsafe_lib_name",
    "test_materialize_uv_editable_ref_into_refuses_a_malformed_manifest",
    "test_materialize_one_uv_editable_ref_accepts_editable_before_path_key_order",
    "test_materialize_one_uv_editable_ref_scopes_rewrite_to_uv_sources_table",
    "test_materialize_one_uv_editable_ref_copies_files_outside_the_four_known_subpaths",
    "test_materialize_uv_editable_ref_into_rewrites_all_aliases_of_the_same_lib",
    "test_materialize_one_uv_editable_ref_accepts_a_quoted_key_and_trailing_comment",
    "test_materialize_one_uv_editable_ref_accepts_a_single_quoted_path",
    "test_materialize_complete_nested_closure_without_editable_edges",
    "test_materialize_uv_editable_refs_fixes_up_a_nested_canonical_dependency",
    "test_materialize_uv_editable_refs_nested_dependency_already_materialized",
    "test_materialize_nested_uv_editable_refs_refuses_a_missing_editable_true",
    "test_materialize_nested_uv_editable_refs_refuses_a_path_traversal_raw_path",
    "test_materialize_nested_uv_editable_refs_refuses_a_non_directory_sibling",
    "test_materialize_nested_uv_editable_refs_refuses_stale_mismatched_content",
)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original UV-reference control: {_name}"
    globals()[_name] = getattr(_original, _name)
