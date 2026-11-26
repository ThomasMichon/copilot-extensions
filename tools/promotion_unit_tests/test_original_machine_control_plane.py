"""Export whole original control-plane parsers without file loading."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
for _library in ("machine-transport", "remote-login-shell"):
    _source = str(_root / "libs" / _library / "src")
    if _source not in sys.path:
        sys.path.insert(0, _source)
_path = _root / "libs" / "machine-transport" / "tests" / "test_control_plane.py"
_spec = importlib.util.spec_from_file_location("promotion_original_machine_control_plane", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_pair_preserves_roles_and_legacy_launch_projection",
    "test_legacy_declaration_does_not_invent_a_knowledge_repository",
    "test_pair_without_legacy_projection_is_not_reported_old_daemon_compatible",
    "test_absent_declaration_stays_absent",
    "test_invalid_roles_refuse_without_coercion_or_fallback",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
