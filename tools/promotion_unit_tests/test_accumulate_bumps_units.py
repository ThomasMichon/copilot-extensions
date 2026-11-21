"""Export original pure and fixture-owned version-stamping controls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_accumulate_bumps.py"
_spec = importlib.util.spec_from_file_location("promotion_original_accumulate_bumps_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

isolated = _original.isolated
_SELECTED = (
    "test_bump_version",
    "test_bump_version_rejects_unparseable",
    "test_bump_version_rejects_unknown_type",
    "test_highest_bump",
    "test_pending_bumps_groups_by_plugin",
    "test_consumption_reuses_an_explicit_pending_list_without_reading_again",
    "test_compute_and_apply_writes_all_three_files",
    "test_apply_does_not_bump_metadata_for_other_plugins",
    "test_multiple_changefiles_same_plugin_pick_highest_bump",
    "test_full_cli_apply_consumes_changefiles",
    "test_skips_plugin_with_no_plugin_json",
    "test_is_standalone_consumer_detects_out_of_plugin_tree",
    "test_is_standalone_consumer_rejects_unrecognized_names",
    "test_compute_and_apply_bumps_a_standalone_consumer",
    "test_index_service_bumps_without_marketplace_plugin",
    "test_standalone_write_only_touches_project_table_version",
    "test_standalone_write_supports_single_quoted_toml_version",
    "test_standalone_write_accepts_inline_comment_on_project_header",
    "test_standalone_write_accepts_indented_project_header",
    "test_standalone_write_accepts_quoted_project_table_name",
    "test_apply_rewrites_standalone_consumer_source_fallback",
    "test_apply_rewrites_literal_version_fallbacks",
    "test_next_after_base",
)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original version-stamping control: {_name}"
    globals()[_name] = getattr(_original, _name)
