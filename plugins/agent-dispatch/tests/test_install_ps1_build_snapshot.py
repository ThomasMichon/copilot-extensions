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


def _harness_script(extra_script: str) -> str:
    """The script every test runs under `pwsh`: this file's own stub
    Write-* helpers (matching install.ps1's actual Write-Host-based
    implementations, which never pollute a captured function's pipeline
    return value) plus every function New-PluginBuildSnapshot depends on,
    extracted verbatim from the real install.ps1 so this module proves the
    actual source behaves correctly, not a reimplementation of it."""
    return (
        # Matches install.ps1's own top-level `$ErrorActionPreference = 'Stop'`
        # (line ~78) -- without it, a non-existent $PluginDir's Get-ChildItem
        # error is merely non-terminating under pwsh's own 'Continue' default,
        # which would not reproduce how the real script actually behaves.
        "$ErrorActionPreference = 'Stop'\n"
        "function Write-Ok { param($m) Write-Host \"OK: $m\" }\n"
        "function Write-Warn { param($m) Write-Host \"WARN: $m\" }\n"
        "function Write-Skip { param($m) Write-Host \"SKIP: $m\" }\n"
        + _extract_function_block("Get-SourceKind")
        + "\n\n"
        + _extract_function_block("Enter-PluginSnapshotLock")
        + "\n\n"
        + _extract_function_block("New-PluginBuildSnapshot")
        + "\n\n"
        + extra_script
        + "\n"
    )


def _run_harness(extra_script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_PWSH, "-NoProfile", "-NonInteractive", "-Command", _harness_script(extra_script)],
        capture_output=True,
        text=True,
        env=os.environ,
        timeout=30,
        check=True,
    )


def _marketplace_plugin_dir(tmp_path: Path) -> Path:
    """A path shaped like a real marketplace-installed payload, so
    Get-SourceKind classifies it as 'marketplace' -- the only kind
    New-PluginBuildSnapshot actually snapshots (a local dev checkout is
    left at its own checkout path; see
    test_local_checkout_is_never_snapshotted)."""
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


def test_copies_payload_into_a_versioned_snapshot_under_install_dir(tmp_path: Path) -> None:
    plugin_dir = _marketplace_plugin_dir(tmp_path)
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


def test_local_checkout_is_never_snapshotted(tmp_path: Path) -> None:
    """A local dev checkout's pyproject.toml declares its
    `[tool.uv.sources]` workspace path deps relative to the monorepo root
    (e.g. `../../libs/zdd`), which only resolves from the checkout's own
    location. Copying just $PluginDir's own tree into a flat snapshot would
    orphan those relative paths, breaking the documented
    direct-from-worktree install path local testing relies on -- so a
    checkout (Get-SourceKind returns anything but 'marketplace') must be
    left at its own path entirely, with no snapshot created at all."""
    plugin_dir = tmp_path / "checkout" / "plugins" / "agent-dispatch"
    install_dir = tmp_path / "install"
    _seed_plugin_dir(plugin_dir)

    extra = f"""
$result = New-PluginBuildSnapshot -PluginDir "{plugin_dir}" -InstallDir "{install_dir}" -Version "0.1.0-dev1"
Write-Output "RESULT:$result"
"""
    result = _run_harness(extra)
    snap_dir = Path(result.stdout.split("RESULT:", 1)[1].strip().splitlines()[0])

    assert snap_dir == plugin_dir
    assert not (install_dir / "snapshots").exists()


def test_excludes_vcs_and_build_junk_from_the_snapshot(tmp_path: Path) -> None:
    plugin_dir = _marketplace_plugin_dir(tmp_path)
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


def test_reuses_an_already_valid_snapshot_for_the_same_version(tmp_path: Path) -> None:
    """A marketplace payload's snapshot for the exact requested version
    that already looks valid (has a pyproject.toml) is reused as-is rather
    than deleted and rebuilt byte-identical -- the fast, idempotent path,
    and the one that makes the replacement race below moot for the common
    re-run case. Safe specifically because a marketplace version string is
    a real, immutable released package identity (unlike a local checkout,
    which this function never snapshots at all -- see
    test_local_checkout_is_never_snapshotted -- precisely because its
    version string can stay unchanged across edited, uncommitted source)."""
    plugin_dir = _marketplace_plugin_dir(tmp_path)
    install_dir = tmp_path / "install"
    _seed_plugin_dir(plugin_dir)
    snap_dir = install_dir / "snapshots" / "0.1.0-dev1"
    snap_dir.mkdir(parents=True)
    (snap_dir / "pyproject.toml").write_text("[project]\nmarker = 'original'\n", encoding="utf-8")

    extra = f"""
$result = New-PluginBuildSnapshot -PluginDir "{plugin_dir}" -InstallDir "{install_dir}" -Version "0.1.0-dev1"
Write-Output "RESULT:$result"
"""
    result = _run_harness(extra)
    returned = Path(result.stdout.split("RESULT:", 1)[1].strip().splitlines()[0])

    assert returned == snap_dir
    # Untouched -- not rebuilt from $plugin_dir's own (different) pyproject.toml.
    assert "marker = 'original'" in (snap_dir / "pyproject.toml").read_text(encoding="utf-8")
    assert not (snap_dir / "src").exists()


def test_replaces_an_invalid_existing_snapshot_via_rename_aside(tmp_path: Path) -> None:
    """An existing $snapDir that does NOT look valid (no pyproject.toml --
    e.g. a torn previous write) must still be replaced by a fresh, valid
    snapshot, and the stale copy must not linger afterward."""
    plugin_dir = _marketplace_plugin_dir(tmp_path)
    install_dir = tmp_path / "install"
    _seed_plugin_dir(plugin_dir)
    snap_dir = install_dir / "snapshots" / "0.1.0-dev1"
    (snap_dir / "junk").mkdir(parents=True)
    (snap_dir / "junk" / "torn.txt").write_text("incomplete\n", encoding="utf-8")

    extra = f"""
$result = New-PluginBuildSnapshot -PluginDir "{plugin_dir}" -InstallDir "{install_dir}" -Version "0.1.0-dev1"
Write-Output "RESULT:$result"
"""
    result = _run_harness(extra)
    returned = Path(result.stdout.split("RESULT:", 1)[1].strip().splitlines()[0])

    assert returned == snap_dir
    assert (snap_dir / "pyproject.toml").exists()
    assert not (snap_dir / "junk").exists()
    # No leftover .stale-<pid> sibling directory.
    assert sorted(p.name for p in (install_dir / "snapshots").iterdir()) == ["0.1.0-dev1"]


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


def test_install_stage_is_not_treated_as_already_safe(tmp_path: Path) -> None:
    """$InstallDir also hosts the self-stage area
    ($InstallDir/.install-stage/<ts>-<pid>/), which a normal marketplace
    stamp/install run commonly passes as $PluginDir -- that transient stage
    must NOT be recognized as already-durable (unlike a path under
    $InstallDir/snapshots/): a real versioned snapshot must still be
    created, not skipped as a redundant no-op. COPILOT_PLUGIN_STAGED_FROM
    (set by the self-stage prologue to the ORIGINAL marketplace path)
    mirrors real self-staged execution so Get-SourceKind still resolves
    'marketplace' here, the same as it would for the real daemon."""
    install_dir = tmp_path / "install"
    plugin_dir = install_dir / ".install-stage" / "20261005T120000000-1234" / "agent-dispatch"
    _seed_plugin_dir(plugin_dir)
    original_payload = tmp_path / ".copilot" / "installed-plugins" / "copilot-extensions" / "agent-dispatch"

    extra = f"""
$env:COPILOT_PLUGIN_STAGED_FROM = "{original_payload}"
$result = New-PluginBuildSnapshot -PluginDir "{plugin_dir}" -InstallDir "{install_dir}" -Version "0.1.0-dev1"
Write-Output "RESULT:$result"
"""
    result = _run_harness(extra)
    snap_dir = Path(result.stdout.split("RESULT:", 1)[1].strip().splitlines()[0])

    expected = install_dir / "snapshots" / "0.1.0-dev1"
    assert snap_dir == expected
    assert (snap_dir / "pyproject.toml").exists()


def test_falls_back_to_the_live_payload_when_no_version_is_resolved(tmp_path: Path) -> None:
    plugin_dir = _marketplace_plugin_dir(tmp_path)
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
    """Invoke-Stamp persists whatever this function returns as the
    self-provisioning binstub's durable `payload-dir` marker. Without
    -BestEffort (Invoke-Stamp's call site), a real copy failure must THROW
    -- matching the script's own top-level `$ErrorActionPreference =
    'Stop'` -- rather than silently returning $PluginDir and letting
    Invoke-Stamp publish a marker pointing at the wrong (transient)
    directory while reporting success."""
    plugin_dir = _marketplace_plugin_dir(tmp_path)
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
    plugin_dir = _marketplace_plugin_dir(tmp_path)
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
    """The no-op containment check must use a literal prefix comparison,
    not `-like` globbing -- a path containing a literal `[` (a valid, if
    unusual, directory-name character) must not be mis-matched as a
    wildcard character class. COPILOT_PLUGIN_STAGED_FROM forces
    Get-SourceKind to 'marketplace' so this reaches the containment check
    at all (a bracket-laden $PluginDir has no '.copilot/installed-plugins'
    substring of its own)."""
    install_dir = tmp_path / "inst[all]"
    plugin_dir = install_dir / "snapshots" / "0.1.0-dev1"
    _seed_plugin_dir(plugin_dir)
    original_payload = tmp_path / ".copilot" / "installed-plugins" / "copilot-extensions" / "agent-dispatch"

    extra = f"""
$env:COPILOT_PLUGIN_STAGED_FROM = "{original_payload}"
$result = New-PluginBuildSnapshot -PluginDir "{plugin_dir}" -InstallDir "{install_dir}" -Version "0.1.0-dev1"
Write-Output "RESULT:$result"
"""
    result = _run_harness(extra)
    snap_dir = Path(result.stdout.split("RESULT:", 1)[1].strip().splitlines()[0])

    # Correctly recognized as already-under-$InstallDir -- a no-op, not a
    # fresh (redundant) copy.
    assert snap_dir == plugin_dir
    assert sorted(p.name for p in (install_dir / "snapshots").iterdir()) == ["0.1.0-dev1"]


def test_concurrent_publishers_never_corrupt_or_lose_the_snapshot(tmp_path: Path) -> None:
    """Two near-simultaneous callers (e.g. two install/stamp actions
    launched back-to-back) racing the SAME $InstallDir/$Version must never
    both observe "no valid snapshot" and each publish their own copy --
    the later publisher's rename-aside+reap step would otherwise retire the
    snapshot the earlier caller already returned and may still be actively
    using. Enter-PluginSnapshotLock serializes the two, so exactly one
    caller does the real copy and the other reuses it (the idempotent fast
    path), and the end state is a single, complete, uncorrupted snapshot
    with no leftover .tmp-/.stale- siblings."""
    plugin_dir = _marketplace_plugin_dir(tmp_path)
    install_dir = tmp_path / "install"
    _seed_plugin_dir(plugin_dir)
    # Enough file content that the copy takes measurable time, widening the
    # real race window between the two processes below.
    bulk_dir = plugin_dir / "src" / "bulk"
    bulk_dir.mkdir()
    for i in range(300):
        (bulk_dir / f"file_{i:03d}.py").write_text(f"x = {i}\n", encoding="utf-8")

    extra = f"""
$result = New-PluginBuildSnapshot -PluginDir "{plugin_dir}" -InstallDir "{install_dir}" -Version "0.1.0-dev1"
Write-Output "RESULT:$result"
"""
    script = _harness_script(extra)
    procs = [
        subprocess.Popen(
            [_PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=os.environ,
        )
        for _ in range(2)
    ]
    results = [p.communicate(timeout=60) for p in procs]

    for (stdout, stderr), proc in zip(results, procs):
        assert proc.returncode == 0, stdout + stderr

    snap_dirs = {
        Path(stdout.split("RESULT:", 1)[1].strip().splitlines()[0]) for stdout, _ in results
    }
    expected = install_dir / "snapshots" / "0.1.0-dev1"
    assert snap_dirs == {expected}

    # A single complete snapshot, nothing torn or duplicated.
    assert (expected / "pyproject.toml").exists()
    assert len(list((expected / "src" / "bulk").glob("file_*.py"))) == 300
    assert sorted(p.name for p in (install_dir / "snapshots").iterdir()) == ["0.1.0-dev1"]


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


def test_snapshot_lock_is_reentrant_on_the_same_thread(tmp_path: Path) -> None:
    """Invoke-Stamp holds an OUTER Enter-PluginSnapshotLock across both
    snapshot creation AND marker publication, while New-PluginBuildSnapshot
    (which it calls) takes its OWN inner lock with the same $InstallDir key
    -- this is only deadlock-free because named-mutex ownership is
    thread-affine (a second WaitOne from the SAME thread re-enters rather
    than blocking on itself). Proves that assumption directly: two nested
    acquisitions for the same install dir, from the same thread/process,
    both succeed without blocking, and the inner release leaves the outer
    acquisition still held."""
    install_dir = tmp_path / "install"

    extra = f"""
$outer = Enter-PluginSnapshotLock -InstallDir "{install_dir}"
$inner = Enter-PluginSnapshotLock -InstallDir "{install_dir}"
Write-Output "BOTH-ACQUIRED"
[void]$inner.ReleaseMutex()
$inner.Dispose()
# Still held by the outer acquisition -- a third WaitOne from a DIFFERENT
# process would time out here; same-thread re-entry would not exercise
# that at all, so this only proves inner release didn't fully unlock it
# by checking the outer release still succeeds cleanly below.
[void]$outer.ReleaseMutex()
$outer.Dispose()
Write-Output "OUTER-RELEASED"
"""
    result = _run_lock_harness(extra)
    assert "BOTH-ACQUIRED" in result.stdout, result.stdout + result.stderr
    assert "OUTER-RELEASED" in result.stdout, result.stdout + result.stderr


def test_version_scoped_locks_for_different_versions_do_not_block_each_other(tmp_path: Path) -> None:
    """Install-Runtime's build lock is scoped by $InstallDir + $Version
    specifically so a wedged build for one version can never block an
    unrelated install of a DIFFERENT, newer version. Proves the two
    resulting mutex names are genuinely independent: holding the lock for
    version A does not prevent acquiring the lock for version B, even with
    a short timeout that would otherwise expose any accidental shared key."""
    install_dir = tmp_path / "install"

    extra = f"""
$lockA = Enter-PluginSnapshotLock -InstallDir "{install_dir}" -Version "0.1.0-dev1"
# If this used the SAME mutex as lockA, a 2s timeout would make this throw.
$lockB = Enter-PluginSnapshotLock -InstallDir "{install_dir}" -Version "0.2.0-dev1" -TimeoutSeconds 2
Write-Output "BOTH-INDEPENDENT-LOCKS-ACQUIRED"
[void]$lockB.ReleaseMutex(); $lockB.Dispose()
[void]$lockA.ReleaseMutex(); $lockA.Dispose()
"""
    result = _run_lock_harness(extra)
    assert "BOTH-INDEPENDENT-LOCKS-ACQUIRED" in result.stdout, result.stdout + result.stderr


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
    force: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    """Invoke-Stamp's version-ordering guard, with everything it depends on
    OTHER than Test-VersionLt/Enter-PluginSnapshotLock/Publish-FileAtomically
    stubbed out (a real snapshot build is irrelevant to the guard itself and
    heavy to construct here). The Deploy-SelfProvisioningBinstub stub writes
    a marker file instead of a plain no-op, so a test can prove whether it
    ran (and thus whether it ran INSIDE the lock, before a guard-triggered
    early return)."""
    install_dir = tmp_path / "install"
    install_dir.mkdir(parents=True)
    plugin_dir = _marketplace_plugin_dir(tmp_path)
    _seed_plugin_dir(plugin_dir)
    if existing_stamped_version is not None:
        (install_dir / "stamped-version").write_text(existing_stamped_version, encoding="utf-8")
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


def test_stamp_force_overrides_the_version_ordering_guard(tmp_path: Path) -> None:
    result, marker, deployed = _run_stamp_harness(
        tmp_path, src_version="0.2.0-dev1", existing_stamped_version="0.2.0-dev2", force=True
    )
    assert marker.read_text(encoding="utf-8") == "0.2.0-dev1", result.stdout + result.stderr
    assert deployed.exists()


def _run_version_lt(a: str, b: str) -> bool:
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        + _extract_function_block("Get-VerTuple")
        + "\n\n"
        + _extract_function_block("Test-VersionLt")
        + "\n\n"
        f'if (Test-VersionLt -A "{a}" -B "{b}") {{ Write-Output "LT:true" }} else {{ Write-Output "LT:false" }}\n'
    )
    result = subprocess.run(
        [_PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        env=os.environ,
        timeout=30,
        check=True,
    )
    assert "LT:" in result.stdout, result.stdout + result.stderr
    return "LT:true" in result.stdout


def test_a_finished_release_is_not_older_than_its_own_dev_prereleases() -> None:
    """Regression: a missing tuple component used to default to 0, making
    "0.2.0" sort as OLDER than "0.2.0-dev1" -- a finished release must
    outrank every devN pre-release build of the same prefix, or
    Invoke-Stamp's version-ordering guard would wrongly skip a legitimate
    dev-to-release promotion."""
    assert _run_version_lt("0.2.0", "0.2.0-dev1") is False
    assert _run_version_lt("0.2.0-dev1", "0.2.0") is True


def test_version_lt_still_compares_differing_prefixes_correctly() -> None:
    assert _run_version_lt("0.1.9-dev5", "0.2.0-dev1") is True
    assert _run_version_lt("0.2.0-dev1", "0.1.9-dev5") is False
    assert _run_version_lt("0.2.0", "0.2.0") is False



