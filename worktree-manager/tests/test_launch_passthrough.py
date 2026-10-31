"""Guards for launch-session.ps1's ``--`` Copilot-passthrough split.

Migrated from plugins/agent-worktrees/tests/test_launch_passthrough.py as part
of the Phase 3b Sub-slice 2a Step 2 cutover (efforts/active/worktree-manager-
control-plane/phase-3b-mux-relocation.md): Worktree Manager's bin/ is now the
sole copy of the interactive mux launch scripts.

agent-bridge spawns a Windows SSH ACP session through the project binstub as::

    test-chamber --json --worktree-id <id> --no-mux --no-update --no-resume \\
        '--' --acp --stdio --allow-all

launch-session.ps1 must split ``$args`` on the ``--`` separator so the Copilot
flags after it (``--acp --stdio --allow-all``) are appended to the Copilot
command rather than leaking into ``agent_worktrees resolve`` -- argparse rejects
unknown flags, which would abort the launch before Copilot ever starts (the
class of failure investigated in test-chamber #1559 / #1677).

Two guards, mirroring test_launch_session_unwrap.py:

* a behavioural test that runs the exact separator-split via ``pwsh -File``
  (the same invocation launch-session.cmd uses), proving both the split logic
  and that ``pwsh -File`` preserves a bare ``--`` in ``$args``; and
* a text drift guard so the split can't be silently removed or a ``param()``
  block reintroduced (which PowerShell's binder would use to reject ``--acp``).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_LAUNCH_PS1 = (
    Path(__file__).resolve().parents[1] / "bin" / "launch-session.ps1"
)

# The exact separator-split launch-session.ps1 applies to $args, reduced to the
# passthrough-relevant branches.  Kept in lockstep with bin/launch-session.ps1;
# the drift guard below fails loudly if the script's snippet is removed.
_SPLIT_SNIPPET = r"""
$CopilotArgs = $args
$FilteredArgs = @()
$CopilotPassthrough = @()
$SeenSeparator = $false
foreach ($arg in $CopilotArgs) {
    if ($SeenSeparator) {
        $CopilotPassthrough += $arg
    } elseif ($arg -eq '--') {
        $SeenSeparator = $true
    } elseif ($arg -eq '--no-update') {
        # consumed into env by the real launcher
    } else {
        $FilteredArgs += $arg
    }
}
Write-Output ("FILTERED=" + ($FilteredArgs -join ' '))
Write-Output ("PASSTHROUGH=" + ($CopilotPassthrough -join ' '))
"""

_PWSH = shutil.which("pwsh") or shutil.which("powershell")


def _run_split(tmp_path: Path, argv: list[str]) -> tuple[list[str], list[str]]:
    """Run the separator-split snippet via ``pwsh -File`` and return
    (filtered_args, copilot_passthrough)."""
    script = tmp_path / "split.ps1"
    script.write_text(_SPLIT_SNIPPET, encoding="utf-8")
    out = subprocess.run(
        [_PWSH, "-NoProfile", "-NoLogo", "-File", str(script), *argv],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    filtered: list[str] = []
    passthrough: list[str] = []
    for line in out.splitlines():
        if line.startswith("FILTERED="):
            filtered = line[len("FILTERED="):].split()
        elif line.startswith("PASSTHROUGH="):
            passthrough = line[len("PASSTHROUGH="):].split()
    return filtered, passthrough


@pytest.mark.skipif(_PWSH is None, reason="pwsh/powershell not available")
def test_separator_routes_copilot_args_to_passthrough(tmp_path: Path):
    """The exact agent-bridge SSH spawn: Copilot flags after ``--`` must land
    in the passthrough (→ appended to $cmd → Copilot), never in the resolve
    args."""
    filtered, passthrough = _run_split(
        tmp_path,
        [
            "--json", "--worktree-id", "wt-1", "--no-mux", "--no-update",
            "--no-resume", "--", "--acp", "--stdio", "--allow-all",
        ],
    )
    # Copilot ACP args survive intact and in order.
    assert passthrough == ["--acp", "--stdio", "--allow-all"]
    # ...and do NOT leak into the args handed to `agent_worktrees resolve`.
    for leaked in ("--acp", "--stdio", "--allow-all", "--"):
        assert leaked not in filtered
    # The resolve-bound args are preserved (minus the consumed --no-update).
    assert filtered == ["--json", "--worktree-id", "wt-1", "--no-mux",
                        "--no-resume"]


@pytest.mark.skipif(_PWSH is None, reason="pwsh/powershell not available")
def test_no_separator_keeps_all_args_out_of_passthrough(tmp_path: Path):
    """Without a ``--`` separator nothing is treated as Copilot passthrough."""
    filtered, passthrough = _run_split(
        tmp_path, ["--json", "--new", "--no-mux"],
    )
    assert passthrough == []
    assert filtered == ["--json", "--new", "--no-mux"]


def test_launcher_reads_args_not_param_block():
    """The launcher must bind $args (not a param() block); a param() block
    makes PowerShell's parameter binder reject unknown Copilot flags such as
    --acp/--stdio before the split ever runs."""
    text = _LAUNCH_PS1.read_text(encoding="utf-8")
    assert "$CopilotArgs = @($args)" in text


def test_launcher_still_splits_and_forwards_passthrough():
    """Drift guard: the ``--`` split and its forward into the Copilot command
    must both remain, else ACP args leak into `resolve` (and Copilot never
    launches)."""
    text = _LAUNCH_PS1.read_text(encoding="utf-8")
    assert "$CopilotPassthrough = @()" in text
    assert "$arg -eq '--'" in text
    assert "$CopilotPassthrough += $arg" in text
    # the collected passthrough is appended to the Copilot command
    assert "$cmd += $CopilotPassthrough" in text


@pytest.mark.parametrize("shell", ["pwsh", "powershell", "bash"])
@pytest.mark.parametrize("stdio", [False, True])
def test_native_launcher_logs_passthrough_count_not_values(tmp_path, shell, stdio):
    if shell == "bash" and os.name != "posix":
        pytest.skip("POSIX shell contract")
    if shell != "bash" and not shutil.which(shell):
        pytest.skip("PowerShell unavailable")
    private = "private-launch-fixture space ' ; $ ` &"
    passthrough = ["--prompt", private, "--allow-all"]
    if stdio:
        passthrough += ["--stdio"]
    argv = ["--json", "--worktree-id", "fixture", "--", *passthrough]
    env = dict(os.environ, TEMP=str(tmp_path), TMP=str(tmp_path), TMPDIR=str(tmp_path))
    env.pop("WORKTREE_SETUP_LOG", None)
    env.pop("AGENT_WORKTREES_LAUNCH_TRACE", None)
    if shell == "bash":
        source = _LAUNCH_PS1.with_suffix(".sh").read_text("utf-8")
        prefix = source[:source.index("# Recovery escape hatch resolves")]
        script = tmp_path / "prefix.sh"
        script.write_text(
            prefix + '\ninitialize_launch_diagnostics\n'
            'printf "%s\\0" "$@" __PASSTHROUGH__ "${COPILOT_PASSTHROUGH[@]}"\n',
            encoding="utf-8",
        )
        command = ["/bin/bash", str(script), *argv]
    else:
        source = _LAUNCH_PS1.read_text("utf-8")
        prefix = source[:source.index("# Recovery fast-path: skip resolve/picker")]
        script = tmp_path / "prefix.ps1"
        script.write_text(
            prefix + "\nInitialize-LaunchDiagnostics\nConvertTo-Json -Compress -InputObject "
            "@{filtered=@($CopilotArgs);passthrough=@($CopilotPassthrough)}\n",
            encoding="utf-8-sig",
        )
        payload = tmp_path / "arguments.json"
        payload.write_text(json.dumps(argv), encoding="utf-8")
        driver = tmp_path / "driver.ps1"
        driver.write_text(
            "$forward=@(foreach($item in (Get-Content -Raw -LiteralPath $args[1] | ConvertFrom-Json)){[string]$item})\n"
            "& $args[0] @forward\n",
            encoding="utf-8",
        )
        command = [shell, "-NoProfile", "-File", str(driver), str(script), str(payload)]
    result = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", timeout=15, env=env,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    if shell == "bash":
        assert result.stdout.split("\0") == [
            "--json", "--worktree-id", "fixture", "__PASSTHROUGH__", *passthrough, "",
        ]
    else:
        assert json.loads(result.stdout) == {
            "filtered": ["--json", "--worktree-id", "fixture"], "passthrough": passthrough,
        }
    logs = list((tmp_path / "worktree-setup-logs").glob("setup-*.log"))
    assert len(logs) == 1
    log = logs[0].read_text("utf-8-sig")
    assert private not in log
    assert f"Copilot passthrough: {len(passthrough)} argument(s)" in log


@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
def test_native_pane_preparation_logs_count_without_changing_argv(tmp_path, shell):
    if not shutil.which(shell):
        pytest.skip("PowerShell unavailable")
    source = _LAUNCH_PS1.read_text("utf-8")
    prefix = source[:source.index("# Recovery fast-path: skip resolve/picker")]
    start = source.index('    Write-SetupLog "psmux: creating session $sessName"')
    end = source.index("\n    $paneWrapper =", start)
    expected = ["program", "--prompt", "private-pane-fixture ' ; $ ` &", "", "two words"]
    payload = tmp_path / "command.json"
    payload.write_text(json.dumps(expected), encoding="utf-8")
    fixture = tmp_path / "pane.ps1"
    fixture.write_text(
        prefix
        + "\nInitialize-LaunchDiagnostics\n$sessName='fixture-session'\n"
        "$cmd=@(foreach($item in (Get-Content -Raw -LiteralPath $env:FIXTURE_COMMAND | ConvertFrom-Json)){[string]$item})\n"
        + source[start:end]
        + '\n"ARGV=" + (ConvertTo-Json -InputObject @($paneCmd) -Compress)\n',
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-File", str(fixture)],
        capture_output=True, text=True, encoding="utf-8", timeout=15,
        env=dict(os.environ, TEMP=str(tmp_path), TMP=str(tmp_path), FIXTURE_COMMAND=str(payload)),
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    forwarded = [line[5:] for line in result.stdout.splitlines() if line.startswith("ARGV=")]
    assert len(forwarded) == 1
    assert json.loads(forwarded[0]) == expected
    logs = list((tmp_path / "worktree-setup-logs").glob("setup-*.log"))
    assert len(logs) == 1
    log = logs[0].read_text("utf-8-sig")
    assert expected[2] not in log
    assert "psmux: pane command: 5 argument(s)" in log


@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
@pytest.mark.parametrize("exit_code", [0, 7])
def test_native_direct_launch_logs_count_and_preserves_child_argv(tmp_path, shell, exit_code):
    if os.name != "nt" or not shutil.which(shell):
        pytest.skip("Windows launcher contract")
    source = _LAUNCH_PS1.read_text("utf-8")
    prefix = source[:source.index("# Recovery fast-path: skip resolve/picker")]
    append_start = source.index("# Append copilot passthrough args (from after -- separator)")
    append_end = source.index("\nif ($ahpArgs.Count", append_start)
    direct_start = source.index("# ── Direct launch (explicit --no-mux only)")
    expected = ["--prompt", "private-direct-fixture space ' ; $ ` &", "--allow-all"]
    payload = tmp_path / "arguments.json"
    payload.write_text(json.dumps(expected), encoding="utf-8")
    captured = tmp_path / "captured.json"
    child = tmp_path / "direct-child.py"
    child.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]),encoding='utf-8')\n"
        f"sys.exit({exit_code})\n",
        encoding="utf-8",
    )
    fixture = tmp_path / "direct.ps1"
    fixture.write_text(
        prefix
        + "\nInitialize-LaunchDiagnostics\n$noMux=$true; $ahpArgs=@()\n"
        "$plan=[pscustomobject]@{worktree_id='fixture';post_exit=$false}\n"
        "function Write-ActivityLog { param($Event,$WorktreeId,$Fields) }\n"
        "$CopilotPassthrough=@(foreach($item in "
        "(Get-Content -Raw -LiteralPath $env:FIXTURE_ARGUMENTS | ConvertFrom-Json)){[string]$item})\n"
        f"$cmd=@('{getattr(sys, '_base_executable', sys.executable)}','{child}','{captured}')\n"
        + source[append_start:append_end]
        + "\n" + source[direct_start:],
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-File", str(fixture)],
        capture_output=True, text=True, encoding="utf-8", timeout=15, cwd=tmp_path,
        env=dict(os.environ, TEMP=str(tmp_path), TMP=str(tmp_path), FIXTURE_ARGUMENTS=str(payload)),
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == exit_code, result.stdout + result.stderr
    assert json.loads(captured.read_text("utf-8")) == expected
    logs = list((tmp_path / "worktree-setup-logs").glob("setup-*.log"))
    assert len(logs) == 1
    log = logs[0].read_text("utf-8-sig")
    assert expected[1] not in log + result.stdout + result.stderr
    assert "Handing off to setup script: 6 argument(s)" in log
