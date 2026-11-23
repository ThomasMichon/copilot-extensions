"""Original in-process launcher and working-directory classification controls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_tools = Path(__file__).resolve().parents[1]
if str(_tools) not in sys.path:
    sys.path.insert(0, str(_tools))
_path = _tools / "test_plugin_test_command_safety.py"
_spec = importlib.util.spec_from_file_location("promotion_original_command_safety_launcher", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_local_launchers_preserve_mux_scope_checks_and_private_admission",
    "test_lookup_does_not_hide_a_following_cleanup_command",
    "test_powershell_working_directory_invalidates_relative_scope",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
