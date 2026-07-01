"""Guards for launch-session.ps1's ``--`` Copilot-passthrough split.

agent-bridge spawns a Windows SSH ACP session through the project binstub as::

    aperture-labs --json --worktree-id <id> --no-mux --no-update --no-resume \\
        '--' --acp --stdio --allow-all

launch-session.ps1 must split ``$args`` on the ``--`` separator so the Copilot
flags after it (``--acp --stdio --allow-all``) are appended to the Copilot
command rather than leaking into ``agent_worktrees resolve`` -- argparse rejects
unknown flags, which would abort the launch before Copilot ever starts (the
class of failure investigated in aperture-labs #1559 / #1677).

Two guards, mirroring test_launch_session_unwrap.py:

* a behavioural test that runs the exact separator-split via ``pwsh -File``
  (the same invocation launch-session.cmd uses), proving both the split logic
  and that ``pwsh -File`` preserves a bare ``--`` in ``$args``; and
* a text drift guard so the split can't be silently removed or a ``param()``
  block reintroduced (which PowerShell's binder would use to reject ``--acp``).
"""

from __future__ import annotations

import shutil
import subprocess
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
    assert "$CopilotArgs = $args" in text


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
