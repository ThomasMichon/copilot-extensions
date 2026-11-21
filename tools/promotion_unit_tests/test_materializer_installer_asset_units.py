"""Export original fixture-only installer and wrapper materialization controls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_materialize_main.py"
_spec = importlib.util.spec_from_file_location("promotion_original_materializer_installer_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_materialize_installer_engine_ref_into_copies_and_rewrites",
    "test_materialize_installer_engine_ref_into_reports_missing_canonical",
    "test_materialize_installer_engine_ref_into_refuses_an_escaping_reference",
    "test_materialize_installer_engine_ref_into_refuses_duplicate_source_lines",
    "test_rewrite_to_local_refuses_duplicate_source_lines",
    "test_rewrite_to_local_preserves_following_comment_lines",
    "test_rewrite_to_local_preserves_trailing_same_line_comments",
    "test_materialize_installer_engine_ref_into_skips_malformed_local_reference_for_registered_adopter",
    "test_materialize_installer_engine_ref_into_preserves_following_comment_lines",
    "test_materialize_launch_wrapper_assets_into_copies_manifested_files",
    "test_materialize_launch_wrapper_assets_into_reports_missing_canonical_file",
    "test_materialize_launch_wrapper_assets_into_refuses_escaping_source_dir",
)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original installer-asset control: {_name}"
    globals()[_name] = getattr(_original, _name)
