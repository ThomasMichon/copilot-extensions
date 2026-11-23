"""Original module-local POSIX shell/environment classification controls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_tools = Path(__file__).resolve().parents[1]
if str(_tools) not in sys.path:
    sys.path.insert(0, str(_tools))
_path = _tools / "test_plugin_test_command_safety.py"
_spec = importlib.util.spec_from_file_location("promotion_original_command_safety_shell_policy", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_env_launcher_changes_the_effective_mux_environment",
    "test_env_launcher_split_string_and_cwd_are_inspected",
    "test_gnu_env_split_extensions_are_refused_instead_of_misparsed",
    "test_shell_state_changes_invalidate_old_scope_at_classification_and_audit",
    "test_shell_mutation_still_allows_an_explicit_absolute_private_transport",
    "test_shell_namespace_expansions_cannot_qualify_literal_private_paths",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
