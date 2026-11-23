"""Original in-process command classification and private-namespace controls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_tools = Path(__file__).resolve().parents[1]
if str(_tools) not in sys.path:
    sys.path.insert(0, str(_tools))
_path = _tools / "test_plugin_test_command_safety.py"
_spec = importlib.util.spec_from_file_location("promotion_original_command_safety_namespace", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_bounded_commands_and_inspection_are_not_globally_banned",
    "test_nul_command_is_refused_without_truncating_its_inspection",
    "test_redirected_home_and_data_directory_do_not_qualify_a_mux",
    "test_namespaces_are_bound_to_one_runner_and_validated_before_use",
    "test_windows_namespace_is_fresh_and_not_an_environment_only_override",
    "test_posix_tmux_accepts_only_its_documented_private_socket",
    "test_child_arguments_cannot_qualify_the_parent_mux_namespace",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
