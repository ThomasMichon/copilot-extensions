"""Original pure source inventories, not source materialization or Git calls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_tools = Path(__file__).resolve().parents[1]
if str(_tools) not in sys.path:
    sys.path.insert(0, str(_tools))
_path = _tools / "test_reproducer_lab_source.py"
_spec = importlib.util.spec_from_file_location("promotion_original_reproducer_source_inventory", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_only_regular_bounded_tree_metadata_is_accepted",
    "test_unsafe_incomplete_or_unbounded_source_tree_refuses",
    "test_source_tree_aggregate_input_ceilings",
    "test_shared_exact_directory_prefixes_remain_supported",
    "test_prefix_entry_and_utf8_byte_ceilings_are_enforced_before_insertion",
    "test_deep_zero_byte_paths_cannot_expand_preflight_memory_without_bound",
    "test_direct_tree_inspection_also_bounds_listing_bytes",
    "test_listing_limit_counts_utf8_bytes_and_accepts_the_exact_ceiling",
    "test_exact_tree_and_blob_input_ceilings",
    "test_git_sha256_source_tree_and_mixed_identity_refusal",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
