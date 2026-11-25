"""Original declared control-plane and pure execution-identity contracts."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType

_root = Path(__file__).resolve().parents[2]
for _library in ("machine-transport", "remote-login-shell"):
    _source = str(_root / "libs" / _library / "src")
    if _source not in sys.path:
        sys.path.insert(0, _source)

_ORIGINAL_FILES = {
    "control_plane": "libs/machine-transport/tests/test_control_plane.py",
    "execution_identity": "libs/machine-transport/tests/test_execution_identity.py",
}
_originals: dict[str, ModuleType] = {}
for _key, _relative in _ORIGINAL_FILES.items():
    _spec = importlib.util.spec_from_file_location(
        "promotion_original_machine_" + _key, _root / _relative,
    )
    assert _spec is not None and _spec.loader is not None
    _module = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = _module
    _spec.loader.exec_module(_module)
    _originals[_key] = _module

_SELECTIONS = {
    "control_plane": (
        "test_pair_preserves_roles_and_legacy_launch_projection",
        "test_legacy_declaration_does_not_invent_a_knowledge_repository",
        "test_pair_without_legacy_projection_is_not_reported_old_daemon_compatible",
        "test_absent_declaration_stays_absent",
        "test_invalid_roles_refuse_without_coercion_or_fallback",
        "test_real_yaml_consumer_retains_pair_and_old_daemon_scalar",
        "test_bad_yaml_or_root_is_explicit",
        "test_missing_source_is_not_a_successful_absent_relationship",
    ),
    "execution_identity": (
        "test_resolve_identity_qualifies_guest_once",
        "test_resolve_identity_prefers_guest_entry_and_warns_when_missing",
        "test_resolve_identity_rejects_ambiguous_labels_and_warns_on_missing_guest",
        "test_shared_display_names_are_metadata_until_unique_lookup",
        "test_machine_entry_projection_and_resolution",
    ),
}
for _key, _names in _SELECTIONS.items():
    globals().update({name: getattr(_originals[_key], name) for name in _names})
