"""PowerShell execution regression coverage for install.ps1's cross-version
activation-ordering guard (``Invoke-VersionedActivate``,
``Test-ActivationSupersededNow``) and the supersession-abort contracts
``Install-Runtime``/``Invoke-Update`` build on top of it -- split out of
test_install_ps1_build_snapshot.py (round-29 review: TESTING.md directs
splitting a large test module "by behavioral contract, not arbitrary line
count" once it covers several genuinely distinct contracts; this module's
own activation/supersession-ordering contract is unrelated to that module's
build-snapshot/lock-reentrancy contract).

This module actually EXECUTES the extracted functions under `pwsh` to prove
the real install.ps1 behaves correctly, not a reimplementation of it.
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
