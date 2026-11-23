"""Original fixture-owned source inspection without executing child commands."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_tools = Path(__file__).resolve().parents[1]
if str(_tools) not in sys.path:
    sys.path.insert(0, str(_tools))
_path = _tools / "test_plugin_test_command_safety.py"
_spec = importlib.util.spec_from_file_location("promotion_original_command_safety_source_inspection", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_source_preflight_rejects_child_cleanup_without_importing_it",
    "test_source_exec_spawn_and_lookup_controls_are_not_globally_banned",
    "test_comments_mock_expectations_and_owned_process_handles_are_not_calls",
    "test_benign_source_does_not_pay_for_ast_analysis",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
