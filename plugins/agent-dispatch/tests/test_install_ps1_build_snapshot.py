"""PowerShell execution regression coverage for install.ps1's
``New-PluginBuildSnapshot`` -- the function that makes Install-Runtime build
from a durable, version-pinned snapshot under ``$InstallDir/snapshots/<ver>/``
instead of the live, swappable marketplace payload (``$PluginDir``).

Confirmed live incident: a `uv pip install` build's own PEP 517 backend
subprocess cwd'd directly into ``libs/agent-procutil`` INSIDE the live
``~/.copilot/installed-plugins/copilot-extensions/agent-dispatch`` payload
and got stuck there for hours, during which
``copilot plugin update agent-dispatch@copilot-extensions`` failed outright
with ``os error 32`` (ERROR_SHARING_VIOLATION) -- Windows refuses to
delete/replace a directory any live process has as its CWD. This module
actually EXECUTES the extracted function under `pwsh` to prove the snapshot
is really created (and really skipped when already safe), not just that the
source text looks right.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_PS1 = _PLUGIN_ROOT / "scripts" / "install.ps1"
_PWSH = shutil.which("pwsh")

pytestmark = pytest.mark.skipif(_PWSH is None, reason="pwsh is not available")


def _extract_function_block(name: str) -> str:
    """Extract one `function <name> { ... }` block by brace-counting from
    its own opening `{`."""
    text = _INSTALL_PS1.read_text(encoding="utf-8")
    func_start = text.index(f"function {name}")
    brace_start = text.index("{", func_start)
    depth = 0
    i = brace_start
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[func_start : i + 1]
        i += 1
    raise AssertionError(f"unbalanced braces extracting function {name!r}")


def _run_harness(extra_script: str) -> subprocess.CompletedProcess[str]:
    script = (
        # Matches install.ps1's own top-level `$ErrorActionPreference = 'Stop'`
        # (line ~78) -- without it, a non-existent $PluginDir's Get-ChildItem
        # error is merely non-terminating under pwsh's own 'Continue' default,
        # which would not reproduce how the real script actually behaves.
        "$ErrorActionPreference = 'Stop'\n"
        "function Write-Ok { param($m) Write-Host \"OK: $m\" }\n"
        "function Write-Warn { param($m) Write-Host \"WARN: $m\" }\n"
        "function Write-Skip { param($m) Write-Host \"SKIP: $m\" }\n"
        + _extract_function_block("New-PluginBuildSnapshot")
        + "\n\n"
        + extra_script
        + "\n"
    )
    return subprocess.run(
        [_PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        env=os.environ,
        timeout=30,
        check=True,
    )


def _seed_plugin_dir(plugin_dir: Path) -> None:
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (plugin_dir / "src").mkdir()
    (plugin_dir / "src" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (plugin_dir / "libs").mkdir()
    (plugin_dir / ".git").mkdir()
    (plugin_dir / ".git" / "HEAD").write_text("ref: refs/heads/dev\n", encoding="utf-8")
    (plugin_dir / "build").mkdir()
    (plugin_dir / "build" / "stale.txt").write_text("stale\n", encoding="utf-8")


def test_copies_payload_into_a_versioned_snapshot_under_install_dir(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "payload" / "agent-dispatch"
    install_dir = tmp_path / "install"
    _seed_plugin_dir(plugin_dir)

    extra = f"""
$result = New-PluginBuildSnapshot -PluginDir "{plugin_dir}" -InstallDir "{install_dir}" -Version "0.1.0-dev1"
Write-Output "RESULT:$result"
"""
    result = _run_harness(extra)
    out = result.stdout
    assert "RESULT:" in out, result.stderr
    snap_dir = Path(out.split("RESULT:", 1)[1].strip().splitlines()[0])

    expected = install_dir / "snapshots" / "0.1.0-dev1"
    assert snap_dir == expected
    assert (snap_dir / "pyproject.toml").exists()
    assert (snap_dir / "src" / "mod.py").exists()
    # The live $PluginDir is untouched -- the copy is one-directional.
    assert (plugin_dir / "pyproject.toml").exists()


def test_excludes_vcs_and_build_junk_from_the_snapshot(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "payload" / "agent-dispatch"
    install_dir = tmp_path / "install"
    _seed_plugin_dir(plugin_dir)

    extra = f"""
$result = New-PluginBuildSnapshot -PluginDir "{plugin_dir}" -InstallDir "{install_dir}" -Version "0.1.0-dev1"
Write-Output "RESULT:$result"
"""
    result = _run_harness(extra)
    snap_dir = Path(result.stdout.split("RESULT:", 1)[1].strip().splitlines()[0])

    assert not (snap_dir / ".git").exists()
    assert not (snap_dir / "build").exists()


def test_is_a_noop_when_plugin_dir_is_already_under_install_dir(tmp_path: Path) -> None:
    """Re-running from an already-made snapshot (the self-provisioning
    binstub's first-use `provision` dispatch runs install.ps1 FROM the
    snapshot it just staged) must not create a redundant copy-of-a-copy."""
    install_dir = tmp_path / "install"
    plugin_dir = install_dir / "snapshots" / "0.1.0-dev1"
    _seed_plugin_dir(plugin_dir)

    extra = f"""
$result = New-PluginBuildSnapshot -PluginDir "{plugin_dir}" -InstallDir "{install_dir}" -Version "0.1.0-dev1"
Write-Output "RESULT:$result"
"""
    result = _run_harness(extra)
    snap_dir = Path(result.stdout.split("RESULT:", 1)[1].strip().splitlines()[0])

    assert snap_dir == plugin_dir
    # No sibling ".tmp-<pid>" or duplicate snapshot directory was created.
    assert sorted(p.name for p in (install_dir / "snapshots").iterdir()) == ["0.1.0-dev1"]


def test_falls_back_to_the_live_payload_when_no_version_is_resolved(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "payload" / "agent-dispatch"
    install_dir = tmp_path / "install"
    _seed_plugin_dir(plugin_dir)

    extra = f"""
$result = New-PluginBuildSnapshot -PluginDir "{plugin_dir}" -InstallDir "{install_dir}"
Write-Output "RESULT:$result"
"""
    result = _run_harness(extra)
    snap_dir = Path(result.stdout.split("RESULT:", 1)[1].strip().splitlines()[0])

    assert snap_dir == plugin_dir
    assert not (install_dir / "snapshots").exists()


def test_without_best_effort_a_copy_failure_rethrows(tmp_path: Path) -> None:
    """Regression (review finding): Invoke-Stamp persists whatever this
    function returns as the self-provisioning binstub's durable
    `payload-dir` marker. Without -BestEffort (Invoke-Stamp's call site), a
    real copy failure must THROW -- matching the script's own top-level
    `$ErrorActionPreference = 'Stop'` -- rather than silently returning
    $PluginDir and letting Invoke-Stamp publish a marker pointing at the
    wrong (transient) directory while reporting success."""
    plugin_dir = tmp_path / "payload" / "agent-dispatch"
    install_dir = tmp_path / "install"
    # A nonexistent $PluginDir makes Get-ChildItem -LiteralPath throw inside
    # the try block -- a real, generic copy failure, not a validation path.
    install_dir.mkdir(parents=True)

    extra = f"""
try {{
    New-PluginBuildSnapshot -PluginDir "{plugin_dir}" -InstallDir "{install_dir}" -Version "0.1.0-dev1" | Out-Null
    Write-Output "RESULT:no-throw"
}} catch {{
    Write-Output "RESULT:threw"
}}
"""
    result = _run_harness(extra)
    assert "RESULT:threw" in result.stdout, result.stdout + result.stderr


def test_with_best_effort_a_copy_failure_degrades_to_the_live_payload(tmp_path: Path) -> None:
    """The same failure, but from Install-Runtime's -BestEffort call site,
    must degrade gracefully instead of aborting the whole install."""
    plugin_dir = tmp_path / "payload" / "agent-dispatch"
    install_dir = tmp_path / "install"
    install_dir.mkdir(parents=True)

    extra = f"""
try {{
    $result = New-PluginBuildSnapshot -PluginDir "{plugin_dir}" -InstallDir "{install_dir}" -Version "0.1.0-dev1" -BestEffort
    Write-Output "RESULT:$result"
}} catch {{
    Write-Output "RESULT:threw"
}}
"""
    result = _run_harness(extra)
    assert f"RESULT:{plugin_dir}" in result.stdout, result.stdout + result.stderr


def test_containment_check_is_a_literal_prefix_not_a_wildcard_match(tmp_path: Path) -> None:
    """Regression (review finding): the no-op containment check must use a
    literal prefix comparison, not `-like` globbing -- a path containing a
    literal `[` (a valid, if unusual, directory-name character) must not be
    mis-matched as a wildcard character class."""
    install_dir = tmp_path / "inst[all]"
    plugin_dir = install_dir / "snapshots" / "0.1.0-dev1"
    _seed_plugin_dir(plugin_dir)

    extra = f"""
$result = New-PluginBuildSnapshot -PluginDir "{plugin_dir}" -InstallDir "{install_dir}" -Version "0.1.0-dev1"
Write-Output "RESULT:$result"
"""
    result = _run_harness(extra)
    snap_dir = Path(result.stdout.split("RESULT:", 1)[1].strip().splitlines()[0])

    # Correctly recognized as already-under-$InstallDir -- a no-op, not a
    # fresh (redundant) copy.
    assert snap_dir == plugin_dir
    assert sorted(p.name for p in (install_dir / "snapshots").iterdir()) == ["0.1.0-dev1"]
