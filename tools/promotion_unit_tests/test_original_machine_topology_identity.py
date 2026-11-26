"""Export whole original identity functions with explicit topology inputs."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
for _library in ("machine-transport", "remote-login-shell"):
    _source = str(_root / "libs" / _library / "src")
    if _source not in sys.path:
        sys.path.insert(0, _source)
_path = _root / "libs" / "machine-transport" / "tests" / "test_execution_identity.py"
_spec = importlib.util.spec_from_file_location("promotion_original_machine_topology_identity", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_resolve_identity_qualifies_guest_once",
    "test_resolve_identity_prefers_guest_entry_and_warns_when_missing",
    "test_resolve_identity_rejects_ambiguous_labels_and_warns_on_missing_guest",
    "test_shared_display_names_are_metadata_until_unique_lookup",
    "test_machine_entry_projection_and_resolution",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
