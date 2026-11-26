"""Export original pure registry projections without YAML file loading."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
for _library in ("machine-transport", "remote-login-shell"):
    _source = str(_root / "libs" / _library / "src")
    if _source not in sys.path:
        sys.path.insert(0, _source)
_path = _root / "libs" / "machine-transport" / "tests" / "test_registry.py"
_spec = importlib.util.spec_from_file_location("promotion_original_machine_registry_projection", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_legacy_wsl_projection_preserves_native_and_exact_profiles",
    "test_explicit_logical_entries_keep_nonconventional_keys_and_physical_association",
    "test_normalization_refuses_profile_collisions_or_ambiguous_platforms",
    "test_normalization_refuses_generated_key_collision",
    "test_known_platform_still_refuses_multiple_independent_guests",
    "test_identity_suffixes_are_not_stripped_or_inferred",
    "test_known_platform_does_not_fall_back_to_wrong_logical_entry",
    "test_known_platform_can_select_hostname_without_optional_physical_host",
    "test_contextual_guest_selection_derives_conventional_host_association",
    "test_explicit_physical_association_overrides_conventional_guest_suffix",
    "test_legacy_guest_hostname_and_display_metadata_do_not_alias_native_identity",
    "TestMachineName",
    "TestFindMachineEntry",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
