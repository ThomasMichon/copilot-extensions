"""Export original actor-free file-pointer and snapshot controls unchanged."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_materialize_main.py"
_spec = importlib.util.spec_from_file_location("promotion_original_materializer_pointer_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_materialize_expands_file_pointer_from_canonical",
    "test_materialize_skips_file_pointer_with_missing_canonical",
    "test_materialize_refuses_absolute_source_path",
    "test_materialize_refuses_traversal_source_path",
    "test_resolve_within_allows_legitimate_nested_source",
    "test_find_file_pointers_ignores_non_pointer_files",
    "test_file_pointer_candidates_cannot_write_outside_extracted_root",
    "test_parallel_pointer_reads_require_the_windows_process_identity",
    "test_build_materializes_file_pointers_end_to_end",
    "test_main_smoke",
    "test_main_returns_nonzero_when_a_retired_pointer_marker_survives",
    "test_build_overwrites_a_stale_existing_dest",
    "test_materialize_refuses_a_retired_directory_pointer_marker",
)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original materializer control: {_name}"
    globals()[_name] = getattr(_original, _name)
