"""Guards for the launch-session.sh nested-plan unwrap.

Non-interactive resolves (``resolve --json --worktree-id`` / ``--json --new``,
used by agent-bridge ACP launches) emit the bridge's *nested* plan shape::

    {"worktree": {...}, "launch": {"action": "exec", ...}}

launch-session.sh consumes the *flat* plan, so it unwraps the ``launch`` object
when present.  These tests pin that contract: the flat consumer must receive an
``action == "exec"`` plan for both nested and already-flat inputs.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# The exact transformation launch-session.sh applies to resolve's stdout.
# Kept in lockstep with bin/launch-session.sh; the marker assertion below
# fails loudly if the script's snippet is removed or renamed.
_UNWRAP_SNIPPET = (
    "import sys, json\n"
    "d = json.load(sys.stdin)\n"
    "print(json.dumps(d['launch'] if isinstance(d, dict) and 'launch' in d else d))"
)

_LAUNCH_SCRIPT = (
    Path(__file__).resolve().parents[1] / "bin" / "launch-session.sh"
)

_LAUNCH_PS1 = (
    Path(__file__).resolve().parents[1] / "bin" / "launch-session.ps1"
)


def _unwrap(plan: dict) -> dict:
    out = subprocess.run(
        [sys.executable, "-c", _UNWRAP_SNIPPET],
        input=json.dumps(plan),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def test_nested_plan_unwraps_to_launch():
    nested = {
        "worktree": {"id": "wt-1"},
        "launch": {
            "action": "exec",
            "work_dir": "/w/wt-1",
            "cmd": ["copilot", "--acp", "--stdio"],
            "no_mux": True,
        },
    }
    flat = _unwrap(nested)
    assert flat["action"] == "exec"
    assert flat["work_dir"] == "/w/wt-1"
    assert flat["no_mux"] is True


def test_flat_plan_passes_through_unchanged():
    flat_in = {"action": "exec", "work_dir": "/w/wt", "cmd": ["copilot"]}
    assert _unwrap(flat_in) == flat_in


def test_none_action_plan_passes_through():
    assert _unwrap({"action": "none", "exit_code": 0}) == {
        "action": "none",
        "exit_code": 0,
    }


def test_launch_script_contains_unwrap_snippet():
    """Drift guard: the script must still apply the unwrap we test here."""
    text = _LAUNCH_SCRIPT.read_text()
    assert "d['launch'] if isinstance(d, dict) and 'launch' in d else d" in text


def test_bash_launcher_avoids_bash4_mapfile():
    """macOS ships Bash 3.2, so both update argv paths must use portable reads."""
    text = _LAUNCH_SCRIPT.read_text()
    assert "mapfile" not in text
    assert "UPDATE_ARGV=()" in text
    assert "while IFS= read -r _update_arg; do" in text
    assert 'UPDATE_ARGV+=("$_update_arg")' in text
    assert "_RARGV=()" in text
    assert "while IFS= read -r _reconcile_arg; do" in text
    assert '_RARGV+=("$_reconcile_arg")' in text


def test_powershell_launcher_contains_unwrap():
    """The Windows launcher must unwrap the nested plan too, else `--json`
    ACP launches to Windows targets fail ($plan.action is null)."""
    text = _LAUNCH_PS1.read_text()
    assert "$plan.PSObject.Properties.Name -contains 'launch'" in text
    assert "$plan = $plan.launch" in text


def test_launchers_pass_project_to_resolve_and_post_exit():
    """Bare launches run from HOME, so every CLI call must carry project context."""
    ps = _LAUNCH_PS1.read_text()
    sh = _LAUNCH_SCRIPT.read_text()
    assert "$script:LaunchProject = $null" in ps
    assert "$arg -eq '--project'" in ps
    assert "$resolveArgs += @('--project', $script:LaunchProject)" in ps
    assert "$postArgs += @('--project', $script:LaunchProject)" in ps
    assert "$script:LaunchProject = [string]$plan.project" in ps
    assert "$directArgs += @('--project', $script:LaunchProject)" in ps
    assert "$setupArgs += $CopilotPassthrough" in ps
    assert "$relaunchArgs += @('--project', $script:LaunchProject)" in ps
    assert "Invoke-AwPostExit $plan.worktree_id" in ps
    assert 'elif [[ "$arg" == "--project" ]]' in sh
    assert 'resolve_args+=(--project "$LAUNCH_PROJECT")' in sh
    assert 'post_args+=(--project "$LAUNCH_PROJECT")' in sh
    assert "json.load(sys.stdin).get('project','')" in sh
    assert 'direct_args+=(--project "$LAUNCH_PROJECT")' in sh
    assert 'if [[ "$RECOVERY_MODE" == "1" ]]' in sh
    assert 'RECOVERY_ARGS+=("${COPILOT_PASSTHROUGH[@]}")' in sh
    assert 'relaunch_args+=(--project "$LAUNCH_PROJECT")' in sh
    assert 'run_post_exit "$WORKTREE_ID"' in sh
    assert "WORKTREE_PROJECT=" not in ps
    assert "WORKTREE_PROJECT=" not in sh
    assert "WORKTREE_RECOVERY" not in ps
    assert "WORKTREE_RECOVERY" not in sh


def test_launchers_render_status_bar_from_worktree_path():
    """Bare resume launches Copilot in HOME, so the status-updater must render
    from the plan's status_path (the real worktree) -- not work_dir (HOME) --
    or the bar loses the worktree's repo:id4 locus + git disposition."""
    ps = _LAUNCH_PS1.read_text()
    sh = _LAUNCH_SCRIPT.read_text()
    # PowerShell: resolve status_path (fallback work_dir) and feed the updater.
    assert "$plan.PSObject.Properties['status_path']" in ps
    assert "Start-StatusUpdater $sessName $muxStatusPath" in ps
    assert "Start-StatusUpdater $sessName $plan.work_dir" not in ps
    # bash: parse STATUS_PATH (fallback work_dir) and pass it as --path.
    assert "d.get('status_path') or d.get('work_dir','')" in sh
    assert 'spath="${STATUS_PATH:-${WORK_DIR:-$PWD}}"' in sh
    assert '--path "$spath"' in sh


def test_windows_launcher_encodes_wrapped_psmux_pane_argv():
    """The encoded wrapper preserves complete argv through psmux's space join."""
    ps = _LAUNCH_PS1.read_text()
    # The collapse helper must be gone. Wrapped pane argv travels through a
    # space-free payload so absolute executable paths remain one argument.
    assert "ConvertTo-PsmuxPaneCommand" not in ps
    assert "$argsJson = ConvertTo-Json -InputObject @($wrapperArgs) -Compress" in ps
    assert "[Text.Encoding]::Unicode.GetBytes($wrapperScript)" in ps
    assert "'-EncodedCommand', $encodedWrapper" in ps
    assert "$paneCmd = $wrapPrefix + $cmd" not in ps
    assert "& $script:AwPsmuxBin new-session -d -s $sessName" in ps
    assert "-c $plan.work_dir @envFlags @paneCmd" in ps


_TERMINAL = Path(__file__).resolve().parents[1] / "terminal"


def test_windows_launcher_applies_psmux_passthrough_per_session():
    """psmux runs a SEPARATE server per wt-<id> and command-line
    bind-key/unbind-key silently no-op there, so the launcher must `source-file`
    the passthrough fragment per session at BOTH create and join -- restoring the
    per-session-at-launch keybind model that mux-config-decoupling's one-time
    global script lost (regression 25c41b7). psmux-only."""
    ps = _LAUNCH_PS1.read_text()
    assert "function Invoke-AwPsmuxPassthroughSafe" in ps
    # Applied at create AND join (>= 2 call sites).
    assert ps.count("Invoke-AwPsmuxPassthroughSafe $sessName") >= 2


def test_session_options_source_files_passthrough_fragment():
    so = (_TERMINAL / "session-options.ps1").read_text()
    assert "function Invoke-AwPsmuxPassthrough" in so
    assert "source-file -t $Session" in so
    assert "psmux-passthrough.conf" in so


def test_psmux_passthrough_fragment_carries_the_directives():
    frag = (_TERMINAL / "psmux-passthrough.conf").read_text()
    assert "unbind-key -a -T root" in frag
    assert "WheelUpPane" in frag and "WheelDownPane" in frag
    assert "paste-detection off" in frag


def test_apply_mux_keybinds_source_files_every_session():
    """The opt-in/restore script must apply per SERVER (source-file each live
    session), not just the last_session server -- command-line binds no-op."""
    amk = (_TERMINAL / "apply-mux-keybinds.ps1").read_text()
    assert "source-file -t $name $fragment" in amk
    assert "psmux-passthrough.conf" in amk


def test_tmux_launcher_does_not_use_psmux_passthrough():
    """psmux-only: tmux (one shared server) keeps the opt-in apply-mux-keybinds.sh
    so worktree tuning never leaks onto a user's personal tmux sessions."""
    sh = _LAUNCH_SCRIPT.read_text()
    assert "psmux-passthrough.conf" not in sh


def test_launchers_fast_reattach_skips_update_on_live_session():
    """Both launchers must skip the pre-launch update when JOINING an
    already-live `wt-<id>` mux session -- a pure re-attach to the running
    Copilot, for which the runtime/plugin update is irrelevant (it applies on
    that process's next fresh start). The Windows path landed in Slice 1
    (dev329); the bash path is Slice 5 (#4059) parity. Drift guard on both."""
    ps = _LAUNCH_PS1.read_text()
    sh = _LAUNCH_SCRIPT.read_text()
    _skip_log = ("Joining an already-live mux session; skipping pre-launch "
                 "update")
    # Windows: the self-contained probe gates Invoke-UpdateApply.
    assert "function Test-AwJoiningLiveSession" in ps
    assert "$joiningLiveSession = Test-AwJoiningLiveSession" in ps
    assert "if ($joiningLiveSession) {" in ps
    assert _skip_log in ps
    # bash: the mirror probe gates invoke_update_apply.
    assert "aw_joining_live_session() {" in sh
    assert "if aw_joining_live_session; then" in sh
    assert _skip_log in sh
    # The bash probe must key off the tmux session name and honor no-mux.
    assert 'tmux has-session -t "=wt-${_wtid}"' in sh
    assert 'WORKTREE_NO_MUX' in sh


def test_launchers_fail_closed_without_explicit_no_mux():
    """Mux discovery/creation failures must never start a duplicate bare
    Copilot. Direct launch is reserved for the explicit no-mux contract."""
    ps = _LAUNCH_PS1.read_text()
    sh = _LAUNCH_SCRIPT.read_text()

    assert "psmux is required for interactive sessions" in ps
    assert "Write-AwMuxFailure -Reason 'launch_probe_failed'" in ps
    assert "Write-AwMuxFailure -Reason 'create_failed'" in ps
    assert "Direct launch (explicit --no-mux only)" in ps
    assert "reached direct launch without --no-mux" in ps
    assert "Falling back to direct launch" not in ps

    assert "tmux is required for interactive sessions" in sh
    assert "activity_log mux_failed" in sh
    assert "reason=create_failed" in sh
    assert "Direct launch (explicit --no-mux only)" in sh
    assert "reached direct launch without --no-mux" in sh
    assert "Falling back to direct launch" not in sh


def test_windows_failed_psmux_creation_reaps_only_the_named_session():
    """A partially-started PSMux server may own a live pane before returning
    nonzero. Cleanup must target the exact failed session and its descendants."""
    ps = _LAUNCH_PS1.read_text()
    assert "function Stop-AwOwnedPsmuxSession" in ps
    assert "[regex]::Escape($Session)" in ps
    assert "server\\s+-s\\s+$escapedSession" in ps
    assert "WORKTREE_LAUNCH_ID=$($script:LaunchId)" in ps
    assert "Sort-Object Value -Descending" in ps
    assert "[Diagnostics.Process]::GetProcessById($pidValue)" in ps
    assert "$startDeltaMs -gt 1" in ps
    assert ps.count("Stop-AwOwnedPsmuxSession $sessName") == 2


def test_launchers_retry_mux_creation_and_preserve_recovery_context():
    """Transient mux startup failures get bounded automatic recovery, while
    exhaustion names the already-created worktree instead of silently losing
    it when the launcher exits."""
    ps = _LAUNCH_PS1.read_text()
    sh = _LAUNCH_SCRIPT.read_text()

    assert "$maxCreateAttempts = 3" in ps
    assert "Start-Sleep -Milliseconds $retryDelayMs" in ps
    assert "Read-AwMuxRetryChoice $sessName" in ps
    assert "[Console]::IsInputRedirected" in ps
    assert "$CopilotArgs -contains '--stdio'" in ps
    assert "'recoverable=true'" in ps
    assert "The worktree remains at '$preservedPath'" in ps
    assert '"attempts=$totalCreateAttempts"' in ps
    assert "else { 'agent-worktrees' }" in ps

    assert "TMUX_CREATE_MAX_ATTEMPTS=3" in sh
    assert "TMUX_CREATE_ATTEMPT<=TMUX_CREATE_MAX_ATTEMPTS" in sh
    assert 'TMUX_RETRY_PROMPT="$_SHOW_LAUNCH_STATUS"' in sh
    assert '[[ "$_copilot_arg" == "--stdio" ]] && TMUX_RETRY_PROMPT=0' in sh
    assert '"$TMUX_RETRY_PROMPT" == "1" && -t 0 && -t 1' in sh
    assert "recoverable=true" in sh
    assert "The worktree remains at '$PRESERVED_PATH'" in sh
    assert '"attempts=$TMUX_CREATE_TOTAL_ATTEMPTS"' in sh
    assert 'RECOVERY_PROJECT="${LAUNCH_PROJECT:-agent-worktrees}"' in sh


def test_launchers_propagate_attach_failures_without_killing_shared_sessions():
    ps = _LAUNCH_PS1.read_text()
    sh = _LAUNCH_SCRIPT.read_text()

    assert "Failed to attach to existing psmux session" in ps
    assert "Failed to attach to new psmux session" in ps
    assert "Write-AwMuxFailure -Reason 'attach_failed'" in ps
    assert "& $script:AwPsmuxBin --version" in ps
    assert "Write-AwMuxFailure -Reason 'launch_probe_failed' -ExitCode $probeExit" in ps
    assert "exit $probeExit" in ps

    assert "_aw_owned_tmux_session_id" in sh
    assert "display-message -p" in sh
    assert "'#{session_id}'" in sh
    assert 'show-environment -t "$session_id" WORKTREE_LAUNCH_ID' in sh
    assert 'kill-session -t "$session_id"' in sh
    assert "Failed to attach to existing tmux session" in sh
    assert "Failed to attach to new tmux session" in sh
    assert "reason=attach_failed" in sh
    assert sh.count('_aw_cleanup_owned_tmux_session "$TMUX_SESS"') == 1
