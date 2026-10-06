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

Portfolio note: this module's own default collection is intentionally kept
to a single-pwsh-process-per-test smoke contract (each test invokes one
`pwsh` subprocess and asserts its result) -- the cheapest tier that still
exercises the REAL extracted PowerShell, not a reimplementation. The two
tests that deliberately race MULTIPLE concurrent `pwsh` processes against
the same named mutex
(test_concurrent_publishers_never_corrupt_or_lose_the_snapshot,
test_reusing_an_already_valid_snapshot_never_blocks_behind_a_long_held_build_lock)
are genuinely repeated-process, timing-sensitive coverage -- real assurance,
but disproportionate to run unconditionally in every PR's required CI lane
across every platform. They self-skip unless
``AGENT_DISPATCH_RUN_PWSH_RACE_TESTS=1`` is set (mirrors
``tools/test_coverage_guided_selection.py``'s own ``CGS_RUN_INTEGRATION_TEST``
opt-in for the same class of real-subprocess integration coverage) -- run them
explicitly in a local dev loop or a path-gated/manual CI lane, not by default.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_PS1 = _PLUGIN_ROOT / "scripts" / "install.ps1"
_PWSH = shutil.which("pwsh")

pytestmark = pytest.mark.skipif(_PWSH is None, reason="pwsh is not available")

_run_pwsh_race_tests = os.environ.get("AGENT_DISPATCH_RUN_PWSH_RACE_TESTS") == "1"
_skip_pwsh_race = pytest.mark.skipif(
    not _run_pwsh_race_tests,
    reason=(
        "multi-process pwsh mutex-race test -- opt in with "
        "AGENT_DISPATCH_RUN_PWSH_RACE_TESTS=1 (deliberately excluded from the "
        "default/required-CI smoke contract; see this module's own docstring)"
    ),
)



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


@pytest.mark.skipif(os.name != "nt", reason="Windows-only sharing-violation repro")
def test_a_blocked_rename_aside_degrades_instead_of_nesting_the_snapshot(tmp_path: Path) -> None:
    """If the rename-aside of an existing (invalid) $snapDir fails -- e.g.
    Windows still has a file inside it open, exactly the ERROR_SHARING_
    VIOLATION class this whole mechanism exists to avoid -- letting
    execution continue to Move-Item would find $snapDir STILL present:
    PowerShell then treats it as a destination CONTAINER and moves the temp
    tree INSIDE it instead of replacing it, silently returning a root that
    lacks pyproject.toml at the expected top level. The rename must be
    terminating so the outer -BestEffort catch degrades correctly instead
    of either of those silently-wrong outcomes."""
    plugin_dir = _marketplace_plugin_dir(tmp_path)
    install_dir = tmp_path / "install"
    _seed_plugin_dir(plugin_dir)
    snap_dir = install_dir / "snapshots" / "0.1.0-dev1"
    (snap_dir / "junk").mkdir(parents=True)
    locked_file = snap_dir / "junk" / "locked.txt"
    locked_file.write_text("locked\n", encoding="utf-8")

    # A plain Python file handle left open for this block's lifetime blocks
    # a directory rename of $snapDir on Windows (confirmed empirically:
    # IOException/ERROR_ACCESS_DENIED) -- simulating the exact class of
    # open-handle failure this guards against.
    handle = open(locked_file, "rb")
    try:
        extra = f"""
$result = New-PluginBuildSnapshot -PluginDir "{plugin_dir}" -InstallDir "{install_dir}" -Version "0.1.0-dev1" -BestEffort
Write-Output "RESULT:$result"
"""
        result = _run_harness(extra)
    finally:
        handle.close()

    # -BestEffort must degrade to the live payload (not silently return a
    # snapDir that got the temp tree nested one level too deep inside it).
    assert "RESULT:" in result.stdout, result.stdout + result.stderr
    returned = Path(result.stdout.split("RESULT:", 1)[1].strip().splitlines()[0])
    assert returned == plugin_dir, (
        f"expected -BestEffort degrade to the live payload, got {returned}"
    )
    # The stale snapshot dir must NOT have gained a nested temp-tree copy
    # (the exact wrong outcome a non-terminating rename would produce: the
    # real code's own `$snapTmp = "$snapDir.tmp-$PID"` ending up moved
    # INSIDE the still-present $snapDir instead of replacing it).
    assert list(snap_dir.glob("*.tmp-*")) == []


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


@_skip_pwsh_race
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
    """Install-Runtime holds an OUTER, version-scoped Enter-PluginSnapshotLock
    spanning its full build+install+activate sequence, while
    New-PluginBuildSnapshot (which it calls) takes its OWN inner lock with
    the SAME $InstallDir + $Version key -- this is only deadlock-free
    because named-mutex ownership is thread-affine (a second WaitOne from
    the SAME thread re-enters rather than blocking on itself). (Invoke-Stamp
    no longer nests this way: it acquires its version-scoped and global
    locks strictly sequentially, precisely to avoid an AB-BA deadlock
    against this real nested pair -- see Invoke-Stamp's own docstring.)
    Proves the reentrancy assumption directly: two nested acquisitions for
    the same install dir AND version, from the same thread/process, both
    succeed without blocking, and the inner release leaves the outer
    acquisition still held."""
    install_dir = tmp_path / "install"

    extra = f"""
$outer = Enter-PluginSnapshotLock -InstallDir "{install_dir}" -Version "0.1.0-dev1"
$inner = Enter-PluginSnapshotLock -InstallDir "{install_dir}" -Version "0.1.0-dev1"
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


@_skip_pwsh_race
def test_reusing_an_already_valid_snapshot_never_blocks_behind_a_long_held_build_lock(
    tmp_path: Path,
) -> None:
    """Install-Runtime can hold the identical version-scoped mutex for its
    full 30-120+ second package build. A concurrent `stamp` invocation (or
    any other caller) asking for an ALREADY-VALID snapshot of that exact
    version must never queue behind that heavy build and risk timing out
    on its own much shorter default lock timeout -- a published snapshot is
    immutable, so reading it needs no lock at all (exactly like the
    binstub's own first-use read). Proves this directly: a background
    process holds the version-scoped lock for well longer than
    New-PluginBuildSnapshot's own default 20s timeout, while a second,
    separate call for the SAME version (with a valid snapshot already on
    disk) still returns promptly instead of blocking on it."""
    plugin_dir = _marketplace_plugin_dir(tmp_path)
    install_dir = tmp_path / "install"
    _seed_plugin_dir(plugin_dir)
    snap_dir = install_dir / "snapshots" / "0.1.0-dev1"
    snap_dir.mkdir(parents=True)
    (snap_dir / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    # Holds the version-scoped lock for well past New-PluginBuildSnapshot's
    # own default 20s timeout, simulating Install-Runtime's long build.
    holder_script = f"""
$ErrorActionPreference = 'Stop'
{_extract_function_block("Enter-PluginSnapshotLock")}

$held = Enter-PluginSnapshotLock -InstallDir "{install_dir}" -Version "0.1.0-dev1"
Write-Output "LOCK-HELD"
Start-Sleep -Seconds 25
[void]$held.ReleaseMutex()
$held.Dispose()
"""
    holder = subprocess.Popen(
        [_PWSH, "-NoProfile", "-NonInteractive", "-Command", holder_script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=os.environ,
    )
    try:
        # Wait for the holder to actually have the lock before racing it.
        assert holder.stdout is not None
        line = holder.stdout.readline()
        assert "LOCK-HELD" in line, line

        extra = f"""
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$result = New-PluginBuildSnapshot -PluginDir "{plugin_dir}" -InstallDir "{install_dir}" -Version "0.1.0-dev1"
$sw.Stop()
Write-Output "RESULT:$result"
Write-Output "ELAPSED_MS:$($sw.ElapsedMilliseconds)"
"""
        result = _run_harness(extra)
    finally:
        # Only the LOCK needs to stay held for the duration of the call
        # under test above -- once that call has returned (proving it
        # never actually blocked on the lock), there is nothing left to
        # verify by waiting out the holder's own sleep too. Killing it
        # outright (an abandoned-mutex release, same as a crashed process)
        # keeps this test's actual runtime close to the fast-path call's own
        # elapsed time rather than padding every run with the holder's full
        # hold duration.
        holder.kill()
        holder.communicate(timeout=10)

    returned = Path(result.stdout.split("RESULT:", 1)[1].strip().splitlines()[0])
    elapsed_ms = int(result.stdout.split("ELAPSED_MS:", 1)[1].strip().splitlines()[0])
    assert returned == snap_dir
    # Comfortably under the holder's 25s sleep and under the 20s default
    # lock timeout -- proves the lock was never actually acquired for this.
    assert elapsed_ms < 10_000, f"took {elapsed_ms}ms -- blocked behind the held lock"


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
    heavy to construct here). Get-VersionedCurrent is stubbed to return
    `existing_active_version` -- the REAL `current-version` authority a
    direct install/update advances, independent of (and never read by) the
    stamped-version marker this guard also checks. The
    Deploy-SelfProvisioningBinstub stub writes a marker file instead of a
    plain no-op, so a test can prove whether it ran (and thus whether it
    ran INSIDE the lock, before a guard-triggered early return)."""
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
        f'function Get-VersionedCurrent {{ return "{existing_active_version or ""}" }}\n'
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


def _run_activate_harness(
    tmp_path: Path,
    *,
    src_version: str,
    current_active: str | None,
    force: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Invoke-VersionedActivate's cross-version ordering guard, with
    Get-VersionedCurrent stubbed (it shells out to the real
    versioned_runtime.py `current` command, irrelevant to the guard itself)
    and the actual python/venv invocation replaced by a fake "python"
    native script that just drops a marker file -- so a test can prove
    whether the real activation call happened at all, not merely what it
    would have printed. Cross-platform: a `.cmd` on Windows, a `chmod +x`
    shebang shell script everywhere else -- NOT a `pwsh`-invoked script,
    since `pwsh <path>` enforces a literal `.ps1` extension on the script
    argument regardless of platform, and the real Invoke-VersionedActivate
    always builds that argument as `versioned_runtime.py`."""
    install_dir = tmp_path / "install"
    install_dir.mkdir(parents=True)
    activated_marker = tmp_path / "activated"
    if os.name == "nt":
        fake_python = tmp_path / "fake-python.cmd"
        fake_python.write_text(
            f'@echo off\r\necho activated>"{activated_marker}"\r\nexit /b 0\r\n',
            encoding="utf-8",
        )
    else:
        fake_python = tmp_path / "fake-python.sh"
        fake_python.write_text(
            f'#!/bin/sh\necho activated > "{activated_marker}"\nexit 0\n',
            encoding="utf-8",
        )
        fake_python.chmod(0o755)

    script = (
        "$ErrorActionPreference = 'Stop'\n"
        "function Write-Ok { param($m) Write-Host \"OK: $m\" }\n"
        "function Write-Warn { param($m) Write-Host \"WARN: $m\" }\n"
        "function Write-Skip { param($m) Write-Host \"SKIP: $m\" }\n"
        "function Write-Fail { param($m) Write-Host \"FAIL: $m\" }\n"
        "function Write-Step { param($m) Write-Host \"STEP: $m\" }\n"
        "function Test-VenvIsLink { param($p) return $false }\n"
        f'function Get-VersionedCurrent {{ return "{current_active or ""}" }}\n'
        + _extract_function_block("Get-VerTuple")
        + "\n\n"
        + _extract_function_block("Test-VersionLt")
        + "\n\n"
        + _extract_function_block("Enter-PluginSnapshotLock")
        + "\n\n"
        + _extract_function_block("Invoke-VersionedActivate")
        + "\n\n"
        "$VersionedRuntime = $true\n"
        f'$InstallDir = "{install_dir}"\n'
        f'$SrcVersion = "{src_version}"\n'
        f"$Force = ${'true' if force else 'false'}\n"
        f'$VenvPython = "{fake_python}"\n'
        f'$LinkPython = "{fake_python}"\n'
        "$result = Invoke-VersionedActivate\n"
        'Write-Output "RETURNED:$result"\n'
        'Write-Output "SUPERSEDED:$script:ActivationSuperseded"\n'
    )
    # Written to and run as a real .ps1 FILE (not -Command): $PSScriptRoot
    # is an automatic, per-scope variable PowerShell rebinds to "" inside
    # any function defined from a bare -Command string, regardless of a
    # manual top-level assignment -- only a genuine backing script file
    # gives Invoke-VersionedActivate's own Join-Path $PSScriptRoot call a
    # real, non-empty directory to resolve against (the fake "python" below
    # ignores every argument it's passed anyway, so the directory's actual
    # content never matters).
    script_path = tmp_path / "harness.ps1"
    script_path.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [_PWSH, "-NoProfile", "-NonInteractive", "-File", str(script_path)],
        capture_output=True,
        text=True,
        env=os.environ,
        timeout=30,
        check=True,
    )
    return result, activated_marker


def test_activate_proceeds_normally_with_no_prior_active_version(tmp_path: Path) -> None:
    result, activated = _run_activate_harness(tmp_path, src_version="0.2.0-dev1", current_active=None)
    assert "RETURNED:True" in result.stdout, result.stdout + result.stderr
    assert activated.exists()
    assert "SUPERSEDED:False" in result.stdout, result.stdout + result.stderr


def test_activate_proceeds_when_newer_than_currently_active(tmp_path: Path) -> None:
    result, activated = _run_activate_harness(
        tmp_path, src_version="0.2.0-dev2", current_active="0.2.0-dev1"
    )
    assert "RETURNED:True" in result.stdout, result.stdout + result.stderr
    assert activated.exists()
    assert "SUPERSEDED:False" in result.stdout, result.stdout + result.stderr


def test_activate_skips_when_older_than_currently_active(tmp_path: Path) -> None:
    """A slower, older-version build that started first can still finish
    (health gate + mark-complete) AFTER a faster, newer-version build
    already activated -- since the two run under independent, version-scoped
    build locks and are never serialized against each other during the
    build itself. The cross-version ordering guard inside
    Invoke-VersionedActivate must catch this at the one point where it
    matters (the actual activate/publish call) and skip rather than
    silently regress `current-version`.

    Skipping the marker write is not, on its own, enough: $script:
    ActivationSuperseded must also come back true, so Install-Runtime and
    Invoke-Update can each abort their own remaining steps (manifest/
    verify/PATH/pivot; coordinator cutover) for THIS invocation instead of
    publishing or cutting over from this invocation's own now-stale
    $VenvPython/$LinkPython build."""
    result, activated = _run_activate_harness(
        tmp_path, src_version="0.2.0-dev1", current_active="0.2.0-dev2"
    )
    assert "Not activating" in result.stdout, result.stdout + result.stderr
    assert "RETURNED:True" in result.stdout, result.stdout + result.stderr
    assert not activated.exists()
    assert "SUPERSEDED:True" in result.stdout, result.stdout + result.stderr


def test_activate_force_overrides_the_cross_version_ordering_guard(tmp_path: Path) -> None:
    result, activated = _run_activate_harness(
        tmp_path, src_version="0.2.0-dev1", current_active="0.2.0-dev2", force=True
    )
    assert "RETURNED:True" in result.stdout, result.stdout + result.stderr
    assert activated.exists()
    assert "SUPERSEDED:False" in result.stdout, result.stdout + result.stderr


def _run_superseded_now_harness(
    tmp_path: Path, *, src_version: str, current_active: str | None, activation_superseded: bool
) -> subprocess.CompletedProcess[str]:
    """Test-ActivationSupersededNow in isolation, with $script:ActivationSuperseded
    pre-seeded directly (bypassing Invoke-VersionedActivate entirely) -- this
    proves the function's OWN live-reread behavior, independent of whichever
    path set that variable."""
    install_dir = tmp_path / "install"
    install_dir.mkdir(parents=True)
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        f'function Get-VersionedCurrent {{ return "{current_active or ""}" }}\n'
        + _extract_function_block("Test-ActivationSupersededNow")
        + "\n\n"
        "$VersionedRuntime = $true\n"
        f'$SrcVersion = "{src_version}"\n'
        f"$script:ActivationSuperseded = ${'true' if activation_superseded else 'false'}\n"
        "$result = Test-ActivationSupersededNow\n"
        'Write-Output "RESULT:$result"\n'
    )
    return subprocess.run(
        [_PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        env=os.environ,
        timeout=30,
        check=True,
    )


def test_superseded_now_false_when_activation_won_and_still_current(tmp_path: Path) -> None:
    result = _run_superseded_now_harness(
        tmp_path, src_version="0.2.0-dev1", current_active="0.2.0-dev1", activation_superseded=False
    )
    assert "RESULT:False" in result.stdout, result.stdout + result.stderr


def test_superseded_now_true_when_activation_itself_was_superseded(tmp_path: Path) -> None:
    result = _run_superseded_now_harness(
        tmp_path, src_version="0.2.0-dev1", current_active="0.2.0-dev2", activation_superseded=True
    )
    assert "RESULT:True" in result.stdout, result.stdout + result.stderr


def test_superseded_now_true_when_won_activation_then_overtaken(tmp_path: Path) -> None:
    """This invocation can genuinely WIN its own activation
    ($script:ActivationSuperseded stays False) and only THEN be overtaken by
    a separate, newer build before a caller re-checks -- Test-
    ActivationSupersededNow must catch this via a fresh Get-VersionedCurrent
    read, not just replay the stale False snapshot."""
    result = _run_superseded_now_harness(
        tmp_path, src_version="0.2.0-dev1", current_active="0.2.0-dev2", activation_superseded=False
    )
    assert "RESULT:True" in result.stdout, result.stdout + result.stderr


@pytest.mark.guard
def test_install_runtime_aborts_remaining_publication_when_superseded() -> None:
    """A superseded invocation must not fall through to Write-Manifest,
    verification, PATH, or Register-PickerPivot -- all of which would
    publish/report through THIS invocation's own (older, losing)
    $VenvPython/$LinkPython build over the already-active newer one's.
    Structural check (a full venv build is too heavy here): the activation
    call must be immediately followed by a Test-ActivationSupersededNow
    check that returns before Write-Manifest, within the still-open
    try/finally that releases buildMutex."""
    text = _INSTALL_PS1.read_text(encoding="utf-8")
    idx = text.index("function Install-Runtime")
    body = text[idx : text.index("\nfunction Write-Manifest", idx)]
    activate_idx = body.index("if (-not (Invoke-VersionedActivate)) { exit 1 }")
    guard_idx = body.index("Test-ActivationSupersededNow", activate_idx)
    return_idx = body.index("return", guard_idx)
    finally_idx = body.index("} finally {", activate_idx)
    assert guard_idx > activate_idx, "the supersession check must come after the activate call"
    assert return_idx < finally_idx, (
        "the supersession check must return before the buildMutex-release finally block "
        "(while still inside the try, so the mutex is still released normally)"
    )


@pytest.mark.guard
def test_invoke_update_aborts_cutover_when_superseded() -> None:
    """A superseded `update` invocation must not drive
    Invoke-CoordinatorCutover/Confirm-CoordinatorRunning from its own
    (older, losing) $VenvPython/$LinkPython build -- that would cut the
    ALREADY-newer, already-active coordinator OVER to a stale one. Structural
    check: a Test-ActivationSupersededNow guard and its `return` must appear
    between the Install-Runtime call and the cutover block, AND again
    immediately before the actual Invoke-CoordinatorCutover call (the live
    re-check closing the "won activation, then overtaken" window a
    one-time post-activation snapshot alone cannot catch), and that the
    SAME lock Invoke-VersionedActivate uses wraps the second re-check
    through the real cutover call -- closing the window where a newer
    invocation could activate and complete ITS OWN cutover while this one
    is merely queued on the cutover subprocess's own internal lease."""
    text = _INSTALL_PS1.read_text(encoding="utf-8")
    idx = text.index("function Invoke-Update")
    body = text[idx : text.index("\nfunction Invoke-Start", idx)]
    install_idx = body.index("Install-Runtime")
    guard_idx = body.index("Test-ActivationSupersededNow", install_idx)
    return_idx = body.index("return", guard_idx)
    # The literal call site ("$didCutover = Invoke-CoordinatorCutover"), not
    # just any mention of the name -- the surrounding comments legitimately
    # reference it by name too.
    cutover_idx = body.index("= Invoke-CoordinatorCutover")
    assert install_idx < guard_idx < return_idx < cutover_idx
    # A SECOND guard, strictly between the first one and the actual cutover
    # call -- the live re-check right at the point of action. Search for it
    # starting AFTER the first guard's own `return` (not merely after the
    # first guard's own index), since that guard's explanatory comment
    # itself mentions "Test-ActivationSupersededNow" by name.
    second_guard_idx = body.index("Test-ActivationSupersededNow", return_idx)
    assert return_idx < second_guard_idx < cutover_idx, (
        "a second, immediate Test-ActivationSupersededNow re-check must sit "
        "directly before Invoke-CoordinatorCutover, not just once right after "
        "Install-Runtime"
    )
    # The cutover mutex must be ACQUIRED before the second guard and only
    # RELEASED after the real cutover call AND the coordinator task
    # reconciliation that follows (Install-CoordinatorTask, the fallback
    # Confirm-CoordinatorRunning) -- not released right after
    # Invoke-CoordinatorCutover itself. Releasing it any earlier still lets
    # a newer invocation activate and complete its own cutover in the gap
    # before THIS invocation reaches Install-CoordinatorTask, whose
    # existing-task path could stop/restart the task the newer cutover just
    # promoted.
    lock_acquire_idx = body.index("$cutoverMutex = Enter-PluginSnapshotLock")
    install_task_idx = body.index("Install-CoordinatorTask -NoStart:$didCutover", cutover_idx)
    lock_release_idx = body.index("$cutoverMutex.ReleaseMutex()")
    assert lock_acquire_idx < second_guard_idx, (
        "the cutover mutex must be acquired BEFORE the second live re-check"
    )
    assert cutover_idx < install_task_idx < lock_release_idx, (
        "the cutover mutex must still be held THROUGH the real "
        "Invoke-CoordinatorCutover call AND the coordinator task "
        "reconciliation that follows, not released in between"
    )


@pytest.mark.guard
def test_every_global_activation_lock_acquisition_shares_one_timeout() -> None:
    """Invoke-VersionedActivate, Invoke-Stamp, and Invoke-Update's cutover
    span all acquire the IDENTICAL global (version-independent)
    Enter-PluginSnapshotLock -InstallDir $InstallDir mutex -- the OS lock is
    keyed by name, not by call site, so they are really one shared lock, not
    three independent ones. A caller using a SHORTER timeout than another
    caller might legitimately hold it would throw "Timed out waiting..."
    during a perfectly normal long cutover instead of simply waiting its
    turn. Every acquisition of this specific mutex must therefore use the
    SAME shared timeout variable."""
    text = _INSTALL_PS1.read_text(encoding="utf-8")
    # Every acquisition of the GLOBAL (no -Version) mutex, across the whole
    # file -- deliberately excludes Enter-PluginSnapshotLock's own
    # definition and any VERSION-scoped acquisition (which pass -Version
    # and are independent, differently-keyed mutexes by design).
    global_acquisitions = re.findall(
        r"Enter-PluginSnapshotLock -InstallDir \$InstallDir(?! -Version)[^\n]*",
        text,
    )
    assert len(global_acquisitions) >= 3, (
        f"expected at least 3 global-lock acquisitions, found {len(global_acquisitions)}: "
        f"{global_acquisitions}"
    )
    for line in global_acquisitions:
        assert "$script:GlobalActivationLockTimeoutSeconds" in line or "-TimeoutSeconds" not in line, (
            f"every global-lock acquisition must use the shared timeout variable, not a "
            f"one-off literal: {line!r}"
        )


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



