"""Export whole original mapping-only local-profile contracts."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
for _library in ("machine-transport", "remote-login-shell"):
    _source = str(_root / "libs" / _library / "src")
    if _source not in sys.path:
        sys.path.insert(0, _source)
_path = _root / "libs" / "machine-transport" / "tests" / "test_local_execution_profiles.py"
_spec = importlib.util.spec_from_file_location("promotion_original_machine_local_profiles", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)
pytestmark = _original.pytestmark

_SELECTED = (
    "test_normalized_guest_profile_keeps_declared_guest_customization",
    "test_logical_projection_canonicalizes_roles_without_mutating_raw_profile_values",
    "test_explicit_space_accepts_equivalent_profile_labels_without_relabeling_keys",
    "test_profile_label_normalization_does_not_accept_a_foreign_or_unknown_space",
    "test_literal_host_profile_alias_cannot_override_its_environment",
    "test_projected_legacy_entries_keep_their_original_keys",
    "test_unnamed_profile_does_not_assert_a_conflicting_environment",
    "test_legacy_nullable_profile_metadata_remains_non_authoritative",
    "test_nullable_profile_alias_does_not_hide_an_explicit_platform_conflict",
    "test_non_string_profile_environment_is_an_explicit_validation_error",
    "test_unknown_legacy_profile_labels_do_not_assert_a_foreign_platform",
    "test_legacy_guest_selection_never_retargets_a_native_profile",
    "test_named_profile_environment_uses_shared_normalization",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
