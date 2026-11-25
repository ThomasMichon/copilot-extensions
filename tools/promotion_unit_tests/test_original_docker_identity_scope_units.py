"""Original Docker scope-selection cases without Git or Docker execution."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
if str(_root / "tools") not in sys.path:
    sys.path.insert(0, str(_root / "tools"))
_path = _root / "tools" / "test_docker_identity_ci_scope.py"
_spec = importlib.util.spec_from_file_location("promotion_original_docker_identity_scope", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_selects_contract_surface",
    "test_unrelated_changes_do_not_select_fixture_processes",
    "test_manual_dispatch_does_not_need_git",
    "test_unavailable_base_expands_scope_visibly",
    "test_diff_error_is_not_a_successful_unrelated_verdict",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
