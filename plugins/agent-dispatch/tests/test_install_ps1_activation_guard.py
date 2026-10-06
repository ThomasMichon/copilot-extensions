"""PowerShell execution regression coverage for install.ps1's cross-version
activation-ordering guard (``Invoke-VersionedActivate``,
``Test-ActivationSupersededNow``) and the supersession-abort contracts
``Install-Runtime``/``Invoke-Update`` build on top of it -- split out of
test_install_ps1_build_snapshot.py per TESTING.md's guidance to split a
large test module "by behavioral contract, not arbitrary line count" once
it covers several genuinely distinct contracts; this module's own
activation/supersession-ordering contract is unrelated to that module's
build-snapshot/lock-reentrancy contract.

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

_run_pwsh_race_tests = os.environ.get("AGENT_DISPATCH_RUN_PWSH_RACE_TESTS") == "1"
_skip_pwsh_race = pytest.mark.skipif(
    not _run_pwsh_race_tests,
    reason=(
        "multi-process pwsh ordering-race test -- opt in with "
        "AGENT_DISPATCH_RUN_PWSH_RACE_TESTS=1 (deliberately excluded from the "
        "default/required-CI smoke contract; mirrors "
        "test_install_ps1_build_snapshot.py's own opt-in for the same class "
        "of real-subprocess integration coverage)"
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


def _run_activate_harness(
    tmp_path: Path,
    *,
    src_version: str,
    current_active: str | None,
    current_stamped: str | None = None,
    force: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Invoke-VersionedActivate's cross-version ordering guard, with
    Get-VersionedCurrent stubbed (it shells out to the real
    versioned_runtime.py `current` command, irrelevant to the guard itself)
    and the actual python/venv invocation replaced by a fake "python"
    native script that just drops a marker file -- so a test can prove
    whether the real activation call happened at all, not merely what it
    would have printed. `current_stamped`, when given, is written directly
    to a `stamped-version` marker file -- the second, independent authority
    the guard also compares against (a `stamp` action publishes this
    without ever touching current-version). Cross-platform: a `.cmd` on
    Windows, a `chmod +x` shebang shell script everywhere else -- NOT a
    `pwsh`-invoked script, since `pwsh <path>` enforces a literal `.ps1`
    extension on the script argument regardless of platform, and the real
    Invoke-VersionedActivate always builds that argument as
    `versioned_runtime.py`."""
    install_dir = tmp_path / "install"
    install_dir.mkdir(parents=True)
    if current_stamped is not None:
        (install_dir / "stamped-version").write_text(current_stamped, encoding="utf-8")
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


def test_activate_skips_when_older_than_a_stamped_version_with_no_active_one(tmp_path: Path) -> None:
    """A delayed v1 install racing a v2 `stamp` must not activate and
    strand the v2 snapshot: `stamp` publishes stamped-version WITHOUT ever
    activating, so current-version can be entirely empty when this delayed
    v1 install reaches its own activation guard. Comparing against
    current-version alone would let it through; the dual-authority check
    must also catch the newer stamped-version."""
    result, activated = _run_activate_harness(
        tmp_path, src_version="0.1.0-dev1", current_active=None, current_stamped="0.2.0-dev1"
    )
    assert "Not activating" in result.stdout, result.stdout + result.stderr
    assert "RETURNED:True" in result.stdout, result.stdout + result.stderr
    assert not activated.exists()
    assert "SUPERSEDED:True" in result.stdout, result.stdout + result.stderr


def test_activate_skips_when_older_than_a_stamped_version_newer_than_current(tmp_path: Path) -> None:
    """The stamped authority can be newer than the current-active one even
    when current-version IS set (an older real install is active while a
    newer version has only been stamped, not yet activated) -- the guard
    must pick the newer of the two authorities as its comparison baseline,
    not just current-version."""
    result, activated = _run_activate_harness(
        tmp_path,
        src_version="0.2.0-dev1",
        current_active="0.1.0-dev1",
        current_stamped="0.2.0-dev2",
    )
    assert "Not activating" in result.stdout, result.stdout + result.stderr
    assert not activated.exists()
    assert "SUPERSEDED:True" in result.stdout, result.stdout + result.stderr


def test_activate_proceeds_when_newer_than_a_stale_stamped_version(tmp_path: Path) -> None:
    """An older, already-superseded stamped-version must never block a
    genuinely newer activation -- only the NEWER of the two authorities
    matters as the floor."""
    result, activated = _run_activate_harness(
        tmp_path,
        src_version="0.2.0-dev2",
        current_active="0.2.0-dev1",
        current_stamped="0.1.0-dev1",
    )
    assert "RETURNED:True" in result.stdout, result.stdout + result.stderr
    assert activated.exists()
    assert "SUPERSEDED:False" in result.stdout, result.stdout + result.stderr


def test_activate_force_overrides_the_stamped_version_guard(tmp_path: Path) -> None:
    result, activated = _run_activate_harness(
        tmp_path,
        src_version="0.1.0-dev1",
        current_active=None,
        current_stamped="0.2.0-dev1",
        force=True,
    )
    assert "RETURNED:True" in result.stdout, result.stdout + result.stderr
    assert activated.exists()
    assert "SUPERSEDED:False" in result.stdout, result.stdout + result.stderr


def _activation_race_script(
    *, install_dir: Path, src_version: str, fake_python: Path, delay_ms: int
) -> str:
    """Unlike _run_activate_harness's canned Get-VersionedCurrent stub,
    this one dynamically re-reads a REAL current-version marker file on
    every call -- so a genuinely separate, concurrently-running process's
    real write is what this process's own in-lock comparison observes,
    not a value fixed in advance by the test. `delay_ms` sleeps BEFORE
    entering Invoke-VersionedActivate at all (not inside the lock) --
    purely to make which process reaches the real race window first
    deterministic across runs, without making the OUTCOME itself
    nondeterministic (the guard logic under test is what decides the
    outcome, not timing)."""
    current_version_marker = install_dir / "current-version"
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "function Write-Ok { param($m) Write-Host \"OK: $m\" }\n"
        "function Write-Warn { param($m) Write-Host \"WARN: $m\" }\n"
        "function Write-Skip { param($m) Write-Host \"SKIP: $m\" }\n"
        "function Write-Fail { param($m) Write-Host \"FAIL: $m\" }\n"
        "function Write-Step { param($m) Write-Host \"STEP: $m\" }\n"
        "function Test-VenvIsLink { param($p) return $false }\n"
        "function Get-VersionedCurrent {\n"
        f'    if (Test-Path "{current_version_marker}") {{\n'
        f'        return (Get-Content -Path "{current_version_marker}" -Raw -ErrorAction SilentlyContinue).Trim()\n'
        "    }\n"
        '    return ""\n'
        "}\n"
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
        "$Force = $false\n"
        f'$VenvPython = "{fake_python}"\n'
        f'$LinkPython = "{fake_python}"\n'
        f"Start-Sleep -Milliseconds {delay_ms}\n"
        "$result = Invoke-VersionedActivate\n"
        'Write-Output "RETURNED:$result"\n'
        'Write-Output "SUPERSEDED:$script:ActivationSuperseded"\n'
    )


@_skip_pwsh_race
def test_two_real_processes_racing_different_versions_never_let_the_older_one_win(
    tmp_path: Path,
) -> None:
    """Every other test in this module proves the guard logic via a
    serial, pre-seeded single-process harness. This is the genuine
    multi-process regression the guard logic actually exists for: TWO
    REAL, SEPARATE `pwsh` processes contend for the SAME
    Enter-PluginSnapshotLock global lock and the SAME install_dir,
    racing Invoke-VersionedActivate for two DIFFERENT versions -- the
    newer one (0.2.0-dev2, no artificial delay) and an older one
    (0.1.0-dev1, started at the same time but sleeping briefly before
    entering its own activation call so it deterministically reaches the
    real race window second, i.e. "finishes last"). Proves the older
    process's activation is genuinely skipped against a REAL concurrent
    write from a REAL separate process -- not a value the test fixed in
    advance -- and that it never republishes/overwrites current-version
    with its own stale build."""
    install_dir = tmp_path / "install"
    install_dir.mkdir(parents=True)

    def _make_fake_python(label: str) -> tuple[Path, Path]:
        activated_marker = tmp_path / f"activated-{label}"
        if os.name == "nt":
            fake_python = tmp_path / f"fake-python-{label}.cmd"
            fake_python.write_text(
                "@echo off\r\n"
                f'echo activated>"{activated_marker}"\r\n'
                f'echo %7>"{install_dir / "current-version"}"\r\n'
                "exit /b 0\r\n",
                encoding="utf-8",
            )
        else:
            fake_python = tmp_path / f"fake-python-{label}.sh"
            fake_python.write_text(
                "#!/bin/sh\n"
                f'echo activated > "{activated_marker}"\n'
                f'echo "$7" > "{install_dir / "current-version"}"\n'
                "exit 0\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)
        return fake_python, activated_marker

    fake_python_newer, activated_newer = _make_fake_python("newer")
    fake_python_older, activated_older = _make_fake_python("older")

    script_newer = _activation_race_script(
        install_dir=install_dir, src_version="0.2.0-dev2", fake_python=fake_python_newer, delay_ms=0
    )
    script_older = _activation_race_script(
        install_dir=install_dir, src_version="0.1.0-dev1", fake_python=fake_python_older, delay_ms=800
    )
    script_path_newer = tmp_path / "harness-newer.ps1"
    script_path_older = tmp_path / "harness-older.ps1"
    script_path_newer.write_text(script_newer, encoding="utf-8")
    script_path_older.write_text(script_older, encoding="utf-8")

    proc_newer = subprocess.Popen(
        [_PWSH, "-NoProfile", "-NonInteractive", "-File", str(script_path_newer)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=os.environ,
    )
    proc_older = subprocess.Popen(
        [_PWSH, "-NoProfile", "-NonInteractive", "-File", str(script_path_older)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=os.environ,
    )
    stdout_newer, stderr_newer = proc_newer.communicate(timeout=30)
    stdout_older, stderr_older = proc_older.communicate(timeout=30)

    assert proc_newer.returncode == 0, stdout_newer + stderr_newer
    assert proc_older.returncode == 0, stdout_older + stderr_older

    assert "RETURNED:True" in stdout_newer, stdout_newer + stderr_newer
    assert "SUPERSEDED:False" in stdout_newer, stdout_newer + stderr_newer
    assert activated_newer.exists()

    assert "Not activating" in stdout_older, stdout_older + stderr_older
    assert "RETURNED:True" in stdout_older, stdout_older + stderr_older
    assert "SUPERSEDED:True" in stdout_older, stdout_older + stderr_older
    assert not activated_older.exists(), (
        "the older process must never reach its own real activation call at all"
    )

    # The newer process's real write is what persists -- the older process
    # never overwrote it with its own stale version.
    assert (install_dir / "current-version").read_text(encoding="utf-8").strip() == "0.2.0-dev2"


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
def test_install_runtime_rechecks_stamped_version_before_publishing() -> None:
    """Test-ActivationSupersededNow only re-reads current-version, which
    Invoke-Stamp never touches (stamping deliberately defers activation).
    A newer concurrent stamp can therefore publish a newer stamped-version
    plus its own binstub/manifest/pivot entirely unnoticed by that check,
    and an older Install-Runtime invocation -- having already passed its
    own activation -- would otherwise overwrite that newer launcher
    surface with its own stale one. Structural check: a second guard
    comparing $SrcVersion against stamped-version must sit strictly
    between the Test-ActivationSupersededNow check and the real
    Deploy-SelfProvisioningBinstub call, still inside the same publishMutex
    lock."""
    text = _INSTALL_PS1.read_text(encoding="utf-8")
    idx = text.index("function Install-Runtime")
    body = text[idx : text.index("\nfunction Write-Manifest", idx)]
    first_guard_idx = body.index("Test-ActivationSupersededNow")
    stamped_check_idx = body.index("'stamped-version'", first_guard_idx)
    version_lt_idx = body.index("Test-VersionLt", stamped_check_idx)
    deploy_idx = body.index("Deploy-SelfProvisioningBinstub", version_lt_idx)
    assert first_guard_idx < stamped_check_idx < version_lt_idx < deploy_idx


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
    # Every REAL acquisition (assignment call site) of the GLOBAL (no
    # -Version) mutex, across the whole file -- requires a `$var = `
    # prefix so this only matches genuine call sites, never the
    # explanatory comment a few lines above $script:GlobalActivationLockTimeoutSeconds's
    # own definition, which mentions this exact call shape in prose
    # (and, lacking a real -TimeoutSeconds argument at all, would
    # otherwise need its own carve-out that could just as easily hide a
    # genuine one-off-timeout bug).
    global_acquisitions = re.findall(
        r"\$\w+ = Enter-PluginSnapshotLock -InstallDir \$InstallDir(?! -Version)[^\n]*",
        text,
    )
    assert len(global_acquisitions) >= 3, (
        f"expected at least 3 global-lock acquisitions, found {len(global_acquisitions)}: "
        f"{global_acquisitions}"
    )
    for line in global_acquisitions:
        assert "$script:GlobalActivationLockTimeoutSeconds" in line, (
            f"every global-lock acquisition must use the shared timeout variable, not a "
            f"one-off literal or the 20s default: {line!r}"
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
