"""Export the complete original synthetic-tree compatibility-root scanner tests."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_compat_root_migration.py"
_spec = importlib.util.spec_from_file_location("promotion_original_compat_root_migration", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

repo = _original.repo
_SELECTED = (
    "test_main_rejects_unresolvable_plugin",
    "test_main_accepts_resolvable_plugin",
    "test_cmd_name_rejects_completely_unknown_name",
    "test_cmd_name_reports_done_when_definition_exists_but_no_calls",
    "test_find_core_call_sites_does_not_match_alias_suffix",
    "test_find_core_call_sites_matches_real_alias_call",
    "test_find_core_call_sites_matches_accessor_call_shape",
    "test_root_aliases_detected_when_import_is_indented",
    "test_find_monkeypatch_sites_single_line",
    "test_find_monkeypatch_sites_multi_line",
    "test_find_monkeypatch_sites_detects_dotted_string_patch",
    "test_find_monkeypatch_sites_dotted_patch_is_alias_independent",
    "test_find_monkeypatch_sites_detects_dotted_string_setattr",
    "test_find_monkeypatch_sites_detects_patch_object",
    "test_find_monkeypatch_sites_detects_single_quoted_dotted_patch",
    "test_find_monkeypatch_sites_detects_single_quoted_dotted_setattr",
    "test_find_monkeypatch_sites_detects_single_quoted_alias_setattr",
    "test_find_monkeypatch_sites_detects_single_quoted_patch_object",
    "test_cmd_progress_excludes_alias_suffix_false_positive",
    "test_cmd_progress_counts_dotted_string_setattr",
    "test_cmd_progress_counts_patch_object",
    "test_cmd_progress_counts_single_quoted_patch_object",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
