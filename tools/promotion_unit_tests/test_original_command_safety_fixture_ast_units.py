"""Original source-only AST controls with unchanged fixture/backend doubles."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_tools = Path(__file__).resolve().parents[1]
if str(_tools) not in sys.path:
    sys.path.insert(0, str(_tools))
_path = _tools / "test_plugin_test_command_safety.py"
_spec = importlib.util.spec_from_file_location("promotion_original_command_safety_fixture_ast", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_native_cleanup_signals_only_the_verified_process_incarnation",
    "test_native_startup_failure_keeps_bounded_diagnostics_without_native_execution",
    "test_native_fixture_environment_disables_shell_network_side_effects",
    "test_native_cursor_probe_keeps_the_original_startup_failure",
    "test_native_startup_probes_preserve_the_exact_python_command_without_running_it",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
