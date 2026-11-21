"""Export the original in-process Git metadata protocol controls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_check_large_files.py"
_spec = importlib.util.spec_from_file_location("promotion_original_large_file_protocol", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_final_diff_uses_one_lossless_diff_and_one_metadata_batch",
    "test_final_diff_refuses_failed_or_incomplete_metadata",
    "test_publication_commit_resolution_batches_and_binds_each_reference",
    "test_publication_commit_resolution_refuses_incomplete_or_mismatched_data",
    "test_publication_commit_resolution_retains_missing_ref_and_refuses_batch_injection",
    "test_pinned_annotated_tag_keeps_original_identity_separate_from_commit",
    "test_pinned_publication_objects_refuse_identity_or_peeling_mismatch",
    "test_blob_metadata_batch_deduplicates_objects_not_paths",
    "test_missing_or_malformed_blob_batch_is_not_a_success",
    "test_empty_or_invalid_object_batch_never_spawns_git",
)
for _name in _SELECTED:
    globals()[_name] = getattr(_original, _name)
