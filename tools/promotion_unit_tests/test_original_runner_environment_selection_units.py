"""Original isolated-environment controls, without host-state access."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_tools = Path(__file__).resolve().parents[1]
if str(_tools) not in sys.path:
    sys.path.insert(0, str(_tools))
_path = _tools / "test_run_plugin_tests.py"
_spec = importlib.util.spec_from_file_location("promotion_original_runner_environment_selection", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)
runner_scratch = _original.runner_scratch

_SELECTED = (
    "test_default_environment_redirects_all_mutable_roots",
    "test_optional_file_override_is_validated_only_when_present",
    "test_host_state_opt_in_preserves_credentials_not_session_affinity",
    "test_host_state_requires_explicit_tiers_at_environment_boundary",
    "test_host_state_still_fails_closed_on_escaped_temp",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
