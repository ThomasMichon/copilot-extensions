"""Guards for the post-update `-RuntimePython`/`--runtime-python` patch in
launch-session.ps1 / launch-session.sh (#stale-venv).

``agent_worktrees resolve`` bakes the interpreter that ran it
(``sys.executable``) into the resolved plan's ``cmd`` as a literal
``-RuntimePython``/``--runtime-python <path>`` argument for
default-setup.ps1/.sh. Resolve runs BEFORE the launcher's stage-update
apply step; if that step swaps the runtime venv in between (installing a
new version and pruning the old one's ``pyvenv.cfg``), the baked path can
point at a partially-deleted venv -- its interpreter binary can survive as
a locked leftover file even after ``pyvenv.cfg`` is gone, so the pane
command fails with "failed to locate pyvenv.cfg" instead of launching on
the runtime that's actually still on disk.

Both launchers now rewrite that argument to the just-refreshed runtime
python right after the post-update refresh, mirroring the pre-existing
``#stale-venv`` re-resolve idiom used elsewhere in these scripts (see
``Write-ActivityLog`` / ``Invoke-AwPostExit`` in launch-session.ps1).

Two guards per platform, mirroring test_launch_passthrough.py:

* a behavioural test that runs the exact patch snippet standalone, proving
  a stale path is rewritten and an already-current path is left alone; and
* a text drift guard so the patch can't be silently removed from the real
  launcher script.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_LAUNCH_PS1 = Path(__file__).resolve().parents[1] / "bin" / "launch-session.ps1"
_LAUNCH_SH = Path(__file__).resolve().parents[1] / "bin" / "launch-session.sh"

_PWSH = shutil.which("pwsh") or shutil.which("powershell")
# Prefer a real POSIX bash over the Windows-Apps WSL launcher stub, which
# fails outside a running WSL distro ("The RPC call contains an invalid
# tag") regardless of this script's own content.
_BASH = (
    shutil.which("bash", path=r"C:\Program Files\Git\usr\bin")
    or shutil.which("bash", path=r"C:\Program Files\Git\bin")
    or shutil.which("bash")
)

# The exact PowerShell patch snippet, reduced to the plan-cmd-patching branch.
# Takes the plan's cmd as a JSON array (matching how $plan.cmd really arrives
# -- via `ConvertFrom-Json` on the resolve subprocess's stdout) so dash-led
# elements like `-RuntimePython` round-trip unambiguously, rather than via
# positional CLI args (where a leading `-` is misread as a new parameter
# token). Kept in lockstep with bin/launch-session.ps1; the drift guard below
# fails loudly if the real snippet is removed.
_PS1_PATCH_SNIPPET = r"""
param([string]$PlanCmdJson, [string]$VenvPython)
$planCmd = @($PlanCmdJson | ConvertFrom-Json)
for ($i = 0; $i -lt $planCmd.Count - 1; $i++) {
    if ($planCmd[$i] -in @('-RuntimePython', '--runtime-python')) {
        if ($planCmd[$i + 1] -ne $VenvPython) {
            $planCmd[$i + 1] = $VenvPython
        }
    }
}
Write-Output ($planCmd -join '|')
"""

# The exact bash patch snippet (CMD_ARRAY equivalent), reduced the same way.
_SH_PATCH_SNIPPET = r"""
CMD_ARRAY=("$@")
PYTHON="$AW_TEST_PYTHON"
for _i in "${!CMD_ARRAY[@]}"; do
    if [[ "${CMD_ARRAY[$_i]}" == "--runtime-python" ]]; then
        _next=$((_i + 1))
        if [[ $_next -lt ${#CMD_ARRAY[@]} && "${CMD_ARRAY[$_next]}" != "$PYTHON" ]]; then
            CMD_ARRAY[$_next]="$PYTHON"
        fi
    fi
done
(IFS='|'; echo "${CMD_ARRAY[*]}")
"""


def _run_ps1_patch(tmp_path: Path, plan_cmd: list[str], venv_python: str) -> list[str]:
    script = tmp_path / "patch.ps1"
    script.write_text(_PS1_PATCH_SNIPPET, encoding="utf-8")
    out = subprocess.run(
        [
            _PWSH, "-NoProfile", "-NoLogo", "-File", str(script),
            "-PlanCmdJson", json.dumps(plan_cmd), "-VenvPython", venv_python,
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return out.split("|")


def _run_sh_patch(plan_cmd: list[str], venv_python: str) -> list[str]:
    env = {"AW_TEST_PYTHON": venv_python}
    out = subprocess.run(
        [_BASH, "-c", _SH_PATCH_SNIPPET, "_", *plan_cmd],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    ).stdout.strip()
    return out.split("|")


@pytest.mark.skipif(_PWSH is None, reason="pwsh/powershell not available")
def test_ps1_patch_rewrites_stale_runtime_python(tmp_path: Path):
    plan_cmd = [
        "pwsh.exe", "-NoProfile", "-NoLogo", "-File", "default-setup.ps1",
        "-Machine", "atlas-core", "-ConfigRoot", "C:\\cfg",
        "-RuntimePython", "C:\\runtime\\versions\\1.11.0-dev1\\Scripts\\python.exe",
        "-SessionPath", "C:\\session",
    ]
    fresh = "C:\\runtime\\versions\\1.12.1-dev1\\Scripts\\python.exe"
    patched = _run_ps1_patch(tmp_path, plan_cmd, fresh)
    assert patched[patched.index("-RuntimePython") + 1] == fresh
    # nothing else in the cmd was touched
    assert patched[:6] == plan_cmd[:6]


@pytest.mark.skipif(_PWSH is None, reason="pwsh/powershell not available")
def test_ps1_patch_is_noop_when_already_current(tmp_path: Path):
    fresh = "C:\\runtime\\versions\\1.12.1-dev1\\Scripts\\python.exe"
    plan_cmd = ["default-setup.ps1", "-RuntimePython", fresh]
    patched = _run_ps1_patch(tmp_path, plan_cmd, fresh)
    assert patched == plan_cmd


def test_ps1_launcher_still_has_the_patch():
    """Drift guard: the plan.cmd rewrite must remain in the real launcher."""
    text = _LAUNCH_PS1.read_text(encoding="utf-8")
    assert "#stale-venv" in text
    assert "'-RuntimePython', '--runtime-python'" in text
    assert "$planCmd[$i + 1] = $VenvPython" in text
    assert "$plan.cmd = $planCmd" in text


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_sh_patch_rewrites_stale_runtime_python():
    plan_cmd = [
        "bash", "default-setup.sh", "--machine", "atlas-core",
        "--config-root", "/cfg",
        "--runtime-python", "/runtime/versions/1.11.0-dev1/bin/python",
        "--session-path", "/session",
    ]
    fresh = "/runtime/versions/1.12.1-dev1/bin/python"
    patched = _run_sh_patch(plan_cmd, fresh)
    assert patched[patched.index("--runtime-python") + 1] == fresh
    assert patched[:6] == plan_cmd[:6]


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_sh_patch_is_noop_when_already_current():
    fresh = "/runtime/versions/1.12.1-dev1/bin/python"
    plan_cmd = ["default-setup.sh", "--runtime-python", fresh]
    patched = _run_sh_patch(plan_cmd, fresh)
    assert patched == plan_cmd


def test_sh_launcher_still_has_the_patch():
    """Drift guard: the CMD_ARRAY rewrite must remain in the real launcher."""
    text = _LAUNCH_SH.read_text(encoding="utf-8")
    assert '"${CMD_ARRAY[$_i]}" == "--runtime-python"' in text
    assert 'CMD_ARRAY[$_next]="$PYTHON"' in text
