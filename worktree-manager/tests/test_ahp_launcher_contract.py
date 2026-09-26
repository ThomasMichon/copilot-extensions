"""Migrated from plugins/agent-worktrees/tests/test_ahp_launcher_contract.py
as part of the Phase 3b Sub-slice 2a Step 2 cutover (efforts/active/worktree-
manager-control-plane/phase-3b-mux-relocation.md): Worktree Manager's bin/ is
now the sole copy of the interactive mux launch scripts these tests pin.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_windows_launcher_hard_binds_and_disables_post_exit():
    source = (ROOT / "bin" / "launch-session.ps1").read_text()
    assert "'execution-leg', 'get'" in source
    assert "'session-backend', 'ensure'" not in source
    assert "'session-backend', 'status'" not in source
    assert "-not $joiningLiveSession -and" in source
    assert "$blob = $executionLeg.execution_leg.blob" in source
    assert "ConvertFrom-SecureString $secureToken" in source
    assert "@('-AwAhpTokenFile', $ahpTokenFile)" in source
    assert "$prop.Name -in @(" in source
    assert "Invoke-AwManagedMuxState -Action activate" in source
    assert "$cmd += $CopilotPassthrough" in source
    assert "$cmd += $ahpArgs" in source
    assert "$arg -like '--ahp=*' -or $arg -like '--resume=*'" in source
    assert "'--ahp', $endpointUrl" in source
    assert '"--resume=$sessionId"' in source
    assert "$plan.post_exit = $false" in source
    assert "gh auth token --user" in source
    assert "launching via the Worktree Manager Picker" in source

    wrapper = (ROOT / "bin" / "pane-wrapper.ps1").read_text()
    assert "$key -eq '-AwAhpTokenFile'" in wrapper
    assert "ConvertTo-SecureString" in wrapper
    assert "$startInfo.Environment['GH_TOKEN'] = $ahpChildToken" in wrapper
    assert "$ahpChildToken = $null" in wrapper


def test_posix_launcher_hard_binds_and_disables_post_exit():
    source = (ROOT / "bin" / "launch-session.sh").read_text()
    assert "execution-leg get" in source
    assert '--worktree-id "$WORKTREE_ID" --json' in source
    assert "session-backend ensure" not in source
    assert "session-backend status" not in source
    assert '"$_JOINING_LIVE" != "1"' in source
    assert "AHP_TOKEN_DIR=$(mktemp -d" in source
    assert 'AHP_TOKEN_FILE="$AHP_TOKEN_DIR/token"' in source
    assert '--aw-ahp-token-file "$AHP_TOKEN_FILE"' in source
    assert "TMUX_ENV_FLAGS+=(" in source
    assert '-e "COPILOT_CLI_ENABLED_FEATURE_FLAGS=' in source
    assert "env -u GH_TOKEN -u GITHUB_TOKEN" in source
    assert "-u COPILOT_CLI_ENABLED_FEATURE_FLAGS tmux" in source
    assert '-e "GH_TOKEN=' not in source
    assert '_aw_managed_mux_state activate "$TMUX_SESS"' in source
    wrapper = (ROOT / "bin" / "pane-wrapper.sh").read_text()
    assert "env -u GITHUB_TOKEN -u AGENT_WORKTREES_AHP_AUTH_TOKEN" in wrapper
    assert 'CMD_ARRAY+=("${COPILOT_PASSTHROUGH[@]}")' in source
    assert "--ahp=*|--resume=*" in source
    assert 'CMD_ARRAY+=(--ahp "$_AHP_ENDPOINT"' in source
    assert '"--resume=$_AHP_SESSION")' in source
    assert "POST_EXIT=0" in source
    assert 'gh auth token --user "$_AHP_ACCOUNT"' in source
    assert "launching via the Worktree Manager Picker" in source
