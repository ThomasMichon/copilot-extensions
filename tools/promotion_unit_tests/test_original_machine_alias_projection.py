"""Export whole original alias grids without local-identity observation."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
for _library in ("machine-transport", "remote-login-shell"):
    _source = str(_root / "libs" / _library / "src")
    if _source not in sys.path:
        sys.path.insert(0, _source)
_path = _root / "libs" / "machine-transport" / "tests" / "test_alias_matrix.py"
_spec = importlib.util.spec_from_file_location("promotion_original_machine_alias_projection", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_explicit_identity_names_exclude_legacy_physical_metadata",
    "test_overlapping_legacy_key_and_profile_keep_historical_host_selection",
    "test_overlapping_scoped_key_retains_manual_authority_and_automatic_physical_context",
    "test_profile_only_match_cannot_borrow_the_overlapping_key_exception",
    "test_contextual_alias_mapping_grid",
    "test_unqualified_physical_metadata_requires_context",
    "test_inline_and_supplied_context_conflicts_reject",
    "test_exact_registered_key_and_profile_precede_format_inference",
    "test_native_profile_is_not_a_global_guest_alias",
    "test_guest_key_cannot_borrow_native_context_even_with_legacy_projection",
    "test_parenthesized_label_whitespace_is_owned_by_the_format_parser",
    "test_historical_registry_keys_are_selected_not_renamed",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
