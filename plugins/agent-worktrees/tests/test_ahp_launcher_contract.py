from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_windows_launcher_hard_binds_and_disables_post_exit():
    source = (ROOT / "bin" / "launch-session.ps1").read_text()
    assert "'session-backend', 'ensure'" in source
    assert "'session-backend', 'status'" in source
    assert "-not $joiningLiveSession -and" in source
    assert "$env:AGENT_WORKTREES_AHP_AUTH_TOKEN = $token.Trim()" in source
    assert "Remove-Item Env:AGENT_WORKTREES_AHP_AUTH_TOKEN" in source
    assert "ConvertFrom-SecureString $secureToken" in source
    assert "@('-AwAhpTokenFile', $ahpTokenFile)" in source
    assert "$prop.Name -in @(" in source
    assert "'AGENT_WORKTREES_AHP_AUTH_TOKEN'" in source
    assert "Start-StatusUpdater" in source
    assert "$cmd += $CopilotPassthrough" in source
    assert "$cmd += $ahpArgs" in source
    assert "$arg -like '--ahp=*' -or $arg -like '--resume=*'" in source
    assert "'--ahp', [string]$sessionBackend.endpoint_url" in source
    assert '"--resume=$($sessionBackend.session_id)"' in source
    assert "$plan.post_exit = $false" in source
    assert "gh auth token --user" in source

    wrapper = (ROOT / "bin" / "pane-wrapper.ps1").read_text()
    assert "$key -eq '-AwAhpTokenFile'" in wrapper
    assert "ConvertTo-SecureString" in wrapper
    assert "$startInfo.Environment['GH_TOKEN'] = $ahpChildToken" in wrapper
    assert "$ahpChildToken = $null" in wrapper


def test_posix_launcher_hard_binds_and_disables_post_exit():
    source = (ROOT / "bin" / "launch-session.sh").read_text()
    assert "session-backend ensure" in source
    assert '--worktree-id "$WORKTREE_ID" --json' in source
    assert "session-backend status" in source
    assert '"$_JOINING_LIVE" != "1"' in source
    assert 'AGENT_WORKTREES_AHP_AUTH_TOKEN="$GH_TOKEN"' in source
    assert "AHP_TOKEN_DIR=$(mktemp -d" in source
    assert 'AHP_TOKEN_FILE="$AHP_TOKEN_DIR/token"' in source
    assert '--aw-ahp-token-file "$AHP_TOKEN_FILE"' in source
    assert "TMUX_ENV_FLAGS+=(" in source
    assert '-e "COPILOT_CLI_ENABLED_FEATURE_FLAGS=' in source
    assert "env -u GH_TOKEN -u GITHUB_TOKEN" in source
    assert "-u AGENT_WORKTREES_AHP_AUTH_TOKEN" in source
    assert "-u COPILOT_CLI_ENABLED_FEATURE_FLAGS tmux" in source
    assert '-e "GH_TOKEN=' not in source
    wrapper = (ROOT / "bin" / "pane-wrapper.sh").read_text()
    assert "env -u GITHUB_TOKEN -u AGENT_WORKTREES_AHP_AUTH_TOKEN" in wrapper
    assert 'CMD_ARRAY+=("${COPILOT_PASSTHROUGH[@]}")' in source
    assert "--ahp=*|--resume=*" in source
    assert 'CMD_ARRAY+=(--ahp "$_AHP_ENDPOINT"' in source
    assert '"--resume=$_AHP_SESSION")' in source
    assert "POST_EXIT=0" in source
    assert 'gh auth token --user "$_AHP_ACCOUNT"' in source
    assert "if SESSION_BACKEND_JSON=$(" in source
