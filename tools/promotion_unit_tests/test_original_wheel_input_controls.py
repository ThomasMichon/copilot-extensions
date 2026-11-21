"""Export original in-process archive and selected-record witnesses."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_installer_wheel_inputs.py"
_spec = importlib.util.spec_from_file_location("promotion_original_wheel_input_controls", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)
limits = _original.limits
wheel_path = _original.wheel_path

_OUTSIDE = (
    "test_stdlib_directory_count_boundaries_allow_zip32_and_zip64",
    "test_symlinked_source_refuses",
    "test_bootstrap_interpreter_needs_no_site_packages",
    "test_actual_metadata_cannot_be_hidden_by_rehashed_archive",
    "test_corrupt_deflate_is_a_structured_refusal",
    "test_selected_record_matches_semantic_names_and_versions",
    "test_selected_record_retains_exact_dependency_spelling_contract",
    "test_each_selected_record_binding_refuses",
    "test_actual_dependency_and_extra_changes_refuse",
    "test_compressed_tags_refuse_before_cartesian_allocation",
    "test_selected_filename_tag_expansion_is_also_bounded",
    "test_repeated_compressed_alternatives_retain_exact_wheel_tag_contract",
    "test_email_encoded_words_cannot_silently_change_dependency_metadata",
)
_SELECTED = tuple(
    _name for _name in vars(_original)
    if _name.startswith("test_") and _name not in _OUTSIDE
)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original wheel-input control: {_name}"
    globals()[_name] = getattr(_original, _name)
