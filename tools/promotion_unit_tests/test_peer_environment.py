"""Run the original peer-environment units without copying assertions."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
specification = importlib.util.spec_from_file_location(
    "promotion_original_peer_environment_tests",
    ROOT / "libs/peer-launch/tests/test_peer_environment.py",
)
assert specification and specification.loader
_original = importlib.util.module_from_spec(specification)
sys.modules[specification.name] = _original
specification.loader.exec_module(_original)

test_agent_dispatch_forwards_control_token_command = (
    _original.test_agent_dispatch_forwards_control_token_command
)
test_agent_dispatch_forwards_raw_control_token = (
    _original.test_agent_dispatch_forwards_raw_control_token
)
test_agent_dispatch_omits_unset_control_token_keys = (
    _original.test_agent_dispatch_omits_unset_control_token_keys
)
test_pr_owner_roster_and_state_rebinding = (
    _original.test_pr_owner_roster_and_state_rebinding
)
test_other_peers_never_inherit_pr_owner_state = (
    _original.test_other_peers_never_inherit_pr_owner_state
)
test_agent_dispatch_retains_authority_and_separate_shared_control = (
    _original.test_agent_dispatch_retains_authority_and_separate_shared_control
)
