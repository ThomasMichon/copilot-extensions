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


def test_powershell_launcher_contains_unwrap():
    """The Windows launcher must unwrap the nested plan too, else `--json`
    ACP launches to Windows targets fail ($plan.action is null)."""
    text = _LAUNCH_PS1.read_text()
    assert "$plan.PSObject.Properties.Name -contains 'launch'" in text
    assert "$plan = $plan.launch" in text


def test_launchers_pass_project_to_post_exit():
    """Bare resume runs from HOME, so post-exit must carry project context."""
    ps = _LAUNCH_PS1.read_text()
    sh = _LAUNCH_SCRIPT.read_text()
    assert "$script:LaunchProject = $env:WORKTREE_PROJECT" in ps
    assert "@('--project', $script:LaunchProject)" in ps
    assert "Invoke-AwPostExit $plan.worktree_id" in ps
    assert 'LAUNCH_PROJECT="${WORKTREE_PROJECT:-}"' in sh
    assert 'post_args+=(--project "$LAUNCH_PROJECT")' in sh
    assert 'run_post_exit "$WORKTREE_ID"' in sh


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
    assert '--path "${STATUS_PATH:-${WORK_DIR:-$PWD}}"' in sh


def test_windows_launcher_passes_psmux_pane_verbatim():
    """psmux wraps every pane command in `pwsh -NoLogo -Command "<args>"`. An
    earlier optimization (#102) collapsed `pwsh -File <script> <args>` to a
    single `& '<script>' <args>` string to avoid a second pwsh -- but under
    `-Command`, PowerShell treats the always-appended `--allow-all` as the
    end-of-parameters marker, so it binds POSITIONALLY to a string param
    (-SetupHook/-EnvScript) and never reaches Copilot (auto-approve silently
    lost; "env_script not found: --allow-all"). The launcher must pass the
    command VERBATIM so `pwsh -File` receives its args literally."""
    ps = _LAUNCH_PS1.read_text()
    # The collapse helper must be gone, and new-session must launch @cmd raw.
    assert "ConvertTo-PsmuxPaneCommand" not in ps
    assert "new-session -d -s $sessName -c $plan.work_dir @envFlags @cmd" in ps
