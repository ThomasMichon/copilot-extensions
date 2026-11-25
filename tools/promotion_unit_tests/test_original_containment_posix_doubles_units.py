"""Original POSIX controls retaining their existing OS and process doubles."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
_path = _root / "tools" / "test_plugin_test_containment.py"
_spec = importlib.util.spec_from_file_location("promotion_original_containment_posix_doubles", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_posix_group_waits_until_no_live_members_remain",
    "test_posix_usage_does_not_treat_zombies_as_file_holding_members",
    "test_linux_empty_group_does_not_require_ps",
    "test_partial_control_pipe_allocation_closes_owned_descriptors",
    "test_failed_teardown_releases_owner_before_reaping_controller",
    "test_posix_group_verifies_members_after_sigkill",
    "test_posix_teardown_kills_children_missed_by_term_enumeration",
    "test_posix_teardown_refuses_stale_identity_before_each_signal",
    "test_posix_exit_observation_does_not_reap_controller",
    "test_posix_control_pipe_failure_is_explicit",
    "test_posix_teardown_refuses_unknown_membership",
    "test_posix_teardown_rejects_malformed_ps_membership",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})
