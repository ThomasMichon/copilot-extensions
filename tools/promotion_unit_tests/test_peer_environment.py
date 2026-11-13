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
test_worktrees_read_does_not_inherit_mcp_or_vault_configuration = (
    _original.test_worktrees_read_does_not_inherit_mcp_or_vault_configuration
)
test_machines_peer_rebinds_its_owner_without_foreign_runtime_overrides = (
    _original.test_machines_peer_rebinds_its_owner_without_foreign_runtime_overrides
)
test_agent_dispatch_retains_authority_and_separate_shared_control = (
    _original.test_agent_dispatch_retains_authority_and_separate_shared_control
)
test_vault_rebinds_native_owner_without_caller_credential_selection = (
    _original.test_vault_rebinds_native_owner_without_caller_credential_selection
)
test_vault_suffix_refuses_unsupported_posix_checksum_without_a_fallback = (
    _original.test_vault_suffix_refuses_unsupported_posix_checksum_without_a_fallback
)
test_vault_peer_refuses_unqualified_native_platform_without_owner_fallback = (
    _original.test_vault_peer_refuses_unqualified_native_platform_without_owner_fallback
)
test_windows_peer_launch_binds_executable_without_rewriting_argv = (
    _original.test_windows_peer_launch_binds_executable_without_rewriting_argv
)
