"""PowerShell execution regression coverage for install.ps1's
``Publish-FileAtomically`` helper and ``Invoke-Stamp``'s version-ordering
guard -- split out of test_install_ps1_build_snapshot.py (round-29 review:
TESTING.md directs splitting a large test module "by behavioral contract,
not arbitrary line count" once it covers several genuinely distinct
contracts; this module's own build-snapshot/lock-reentrancy contract is
unrelated to the marker-publication guard covered here).

This module actually EXECUTES the extracted functions under `pwsh` to prove
the real install.ps1 behaves correctly, not a reimplementation of it.
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


def _marketplace_plugin_dir(tmp_path: Path) -> Path:
    """A path shaped like a real marketplace-installed payload, so
    Get-SourceKind classifies it as 'marketplace'."""
    return tmp_path / ".copilot" / "installed-plugins" / "copilot-extensions" / "agent-dispatch"


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


def _run_lock_harness(extra_script: str) -> subprocess.CompletedProcess[str]:
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        + _extract_function_block("Enter-PluginSnapshotLock")
        + "\n\n"
        + _extract_function_block("Publish-FileAtomically")
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


def test_publish_file_atomically_creates_a_new_file(tmp_path: Path) -> None:
    target = tmp_path / "install" / "payload-dir"
    target.parent.mkdir(parents=True)

    extra = f"""
Publish-FileAtomically -Path "{target}" -Content "C:\\snap\\v1" -Encoding ([System.Text.UTF8Encoding]::new($false))
"""
    _run_lock_harness(extra)
    assert target.read_text(encoding="utf-8") == "C:\\snap\\v1"
    # No leftover .tmp-<pid>/.bak-<pid> siblings.
    assert sorted(p.name for p in target.parent.iterdir()) == ["payload-dir"]


def test_publish_file_atomically_replaces_an_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "install" / "payload-dir"
    target.parent.mkdir(parents=True)
    target.write_text("C:\\snap\\old", encoding="utf-8")

    extra = f"""
Publish-FileAtomically -Path "{target}" -Content "C:\\snap\\new" -Encoding ([System.Text.UTF8Encoding]::new($false))
"""
    _run_lock_harness(extra)
    assert target.read_text(encoding="utf-8") == "C:\\snap\\new"
    assert sorted(p.name for p in target.parent.iterdir()) == ["payload-dir"]


def _run_stamp_harness(
    tmp_path: Path,
    *,
    src_version: str,
    existing_stamped_version: str | None,
    existing_active_version: str | None = None,
    force: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    """Invoke-Stamp's version-ordering guard, with everything it depends on
    OTHER than Test-VersionLt/Enter-PluginSnapshotLock/Publish-FileAtomically
    stubbed out (a real snapshot build is irrelevant to the guard itself and
    heavy to construct here). `existing_active_version`, when given, is
    written directly to a `current-version` marker file -- the REAL
    `current-version` authority a direct install/update advances,
    independent of (and never touched by) the stamped-version marker this
    guard also checks. Deliberately NOT routed through a stubbed
    Get-VersionedCurrent: the real Invoke-Stamp reads this marker directly
    as plain text (no interpreter resolution at all), precisely so this
    guard still works when no venv/slot has ever been provisioned -- this
    harness must exercise that exact real behavior, not paper over it with
    a function stub. The Deploy-SelfProvisioningBinstub stub writes a
    marker file instead of a plain no-op, so a test can prove whether it
    ran (and thus whether it ran INSIDE the lock, before a guard-triggered
    early return)."""
    install_dir = tmp_path / "install"
    install_dir.mkdir(parents=True)
    plugin_dir = _marketplace_plugin_dir(tmp_path)
    _seed_plugin_dir(plugin_dir)
    if existing_stamped_version is not None:
        (install_dir / "stamped-version").write_text(existing_stamped_version, encoding="utf-8")
    if existing_active_version is not None:
        (install_dir / "current-version").write_text(existing_active_version, encoding="utf-8")
    deployed_marker = tmp_path / "binstub-deployed"

    script = (
        "$ErrorActionPreference = 'Stop'\n"
        "function Write-Ok { param($m) Write-Host \"OK: $m\" }\n"
        "function Write-Warn { param($m) Write-Host \"WARN: $m\" }\n"
        "function Write-Skip { param($m) Write-Host \"SKIP: $m\" }\n"
        "function Write-Fail { param($m) Write-Host \"FAIL: $m\" }\n"
        # Stubbed: irrelevant to the version-ordering guard under test.
        "function New-PluginBuildSnapshot { param($PluginDir, $InstallDir, $Version) return Join-Path $InstallDir \"snapshots/$Version\" }\n"
        f'function Deploy-SelfProvisioningBinstub {{ Set-Content -Path "{deployed_marker}" -Value "deployed" }}\n'
        + _extract_function_block("Get-VerTuple")
        + "\n\n"
        + _extract_function_block("Test-VersionLt")
        + "\n\n"
        + _extract_function_block("Enter-PluginSnapshotLock")
        + "\n\n"
        + _extract_function_block("Publish-FileAtomically")
        + "\n\n"
        + _extract_function_block("Invoke-Stamp")
        + "\n\n"
        f'$SrcVersion = "{src_version}"\n'
        f'$InstallDir = "{install_dir}"\n'
        f'$LocalBin = "{tmp_path / "localbin"}"\n'
        f'$PluginDir = "{plugin_dir}"\n'
        f"$Force = ${'true' if force else 'false'}\n"
        "Invoke-Stamp\n"
    )
    result = subprocess.run(
        [_PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        env=os.environ,
        timeout=30,
        check=True,
    )
    return result, install_dir / "stamped-version", deployed_marker


def test_stamp_publishes_normally_with_no_prior_stamped_version(tmp_path: Path) -> None:
    result, marker, deployed = _run_stamp_harness(
        tmp_path, src_version="0.2.0-dev1", existing_stamped_version=None
    )
    assert marker.read_text(encoding="utf-8") == "0.2.0-dev1", result.stdout + result.stderr
    assert deployed.exists()


def test_stamp_publishes_normally_when_newer_than_current(tmp_path: Path) -> None:
    result, marker, deployed = _run_stamp_harness(
        tmp_path, src_version="0.2.0-dev2", existing_stamped_version="0.2.0-dev1"
    )
    assert marker.read_text(encoding="utf-8") == "0.2.0-dev2", result.stdout + result.stderr
    assert deployed.exists()


def test_stamp_skips_publishing_when_older_than_current(tmp_path: Path) -> None:
    """A delayed/preempted older-version stamp acquiring the lock AFTER a
    newer one already published must not overwrite the newer markers, OR
    redeploy its own (older) binstub/resolver files over the newer
    invocation's already-deployed ones -- the mutex only serializes writes,
    it doesn't guarantee arrival order."""
    result, marker, deployed = _run_stamp_harness(
        tmp_path, src_version="0.2.0-dev1", existing_stamped_version="0.2.0-dev2"
    )
    assert "Not publishing" in result.stdout, result.stdout + result.stderr
    assert not deployed.exists()
    assert marker.read_text(encoding="utf-8") == "0.2.0-dev2"


def test_stamp_skips_publishing_when_older_than_the_active_install(tmp_path: Path) -> None:
    """A direct install/update advances `current-version` WITHOUT ever
    touching `stamped-version` -- the stamped-version-only guard above
    catches a delayed stamp racing another STAMP, but not one racing a
    real install/update that has since activated a newer build. A delayed
    stamp reaching this guard with NO prior stamped-version at all (so the
    stamped-version check alone would let it through) must still be
    rejected when a newer version is already the real active install."""
    result, marker, deployed = _run_stamp_harness(
        tmp_path,
        src_version="0.2.0-dev1",
        existing_stamped_version=None,
        existing_active_version="0.2.0-dev2",
    )
    assert "Not publishing" in result.stdout, result.stdout + result.stderr
    assert "already-active" in result.stdout, result.stdout + result.stderr
    assert not deployed.exists()
    assert not marker.exists()


def test_stamp_force_overrides_the_active_install_version_guard(tmp_path: Path) -> None:
    result, marker, deployed = _run_stamp_harness(
        tmp_path,
        src_version="0.2.0-dev1",
        existing_stamped_version=None,
        existing_active_version="0.2.0-dev2",
        force=True,
    )
    assert marker.read_text(encoding="utf-8") == "0.2.0-dev1", result.stdout + result.stderr
    assert deployed.exists()


def test_stamp_force_overrides_the_version_ordering_guard(tmp_path: Path) -> None:
    result, marker, deployed = _run_stamp_harness(
        tmp_path, src_version="0.2.0-dev1", existing_stamped_version="0.2.0-dev2", force=True
    )
    assert marker.read_text(encoding="utf-8") == "0.2.0-dev1", result.stdout + result.stderr
    assert deployed.exists()
