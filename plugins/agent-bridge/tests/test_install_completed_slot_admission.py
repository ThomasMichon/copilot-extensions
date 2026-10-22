"""Regression coverage: a completed runtime slot is never rebuilt in place.

ThomasMichon/copilot-extensions#5472: a same-version install/update whose
payload content changed used to drain the live daemon and then rebuild the
already-published ``versions/<v>`` slot in place, and the completion-marker
helper's refusal of that conflicting publication was silently ignored.

These tests run the *actual* installer functions (extracted verbatim from
``scripts/install.ps1`` / ``scripts/install.sh``) against the real shared
``scripts/versioned_runtime.py`` primitive and real slot evidence on disk.
Only the side-effecting seams around the admission point (lock, drain, venv
build, manifest) are stubbed, as sentinels recording whether the installer
proceeded past admission.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _PLUGIN_ROOT / "scripts"
_INSTALL_PS1 = _SCRIPTS / "install.ps1"
_INSTALL_SH = _SCRIPTS / "install.sh"
_PWSH = shutil.which("pwsh")
_VERSION = "1.2.3"
_MARKER = ".install-complete.json"
# Real Git Bash/Python boundaries can exceed the runner's 30s default.
# Keep the item's observation bound aligned with its subprocess bound.
_SH_TIMEOUT_SECONDS = 120

_PROCEED = "proceed"  # admission let the installer continue to build
_NOOP = "noop"  # unchanged, published and active slot: nothing touched
_REFUSED = "refused"  # published slot with other/unverifiable content
_REUSED = "reused"  # matching published slot activated without being touched

_ADMISSION_CASES = [
    ("absent", _PROCEED),
    ("incomplete", _PROCEED),
    ("match-active", _NOOP),
    ("match-inactive", _REUSED),
    ("conflict-active", _REFUSED),
    ("conflict-inactive", _REFUSED),
    ("malformed", _REFUSED),
]


def _bash() -> str | None:
    if os.name == "nt":
        git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
        return str(git_bash) if git_bash.is_file() else None
    return shutil.which("bash")


def _posix(path: Path | str) -> str:
    text = str(path).replace("\\", "/")
    if os.name == "nt" and len(text) > 1 and text[1] == ":":
        text = "/" + text[0].lower() + text[2:]
    return text


def _ps_quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def _extract_ps(name: str, text: str, *, optional: bool = False) -> str:
    start = text.find(f"function {name} {{")
    if start < 0:
        if optional:
            return ""
        raise AssertionError(f"install.ps1 is missing {name}")
    depth = 0
    for i in range(text.index("{", start), len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise AssertionError(f"unbalanced braces extracting {name!r}")


def _extract_sh(name: str, text: str, *, optional: bool = False) -> str:
    start = text.find(f"\n{name}() {{")
    if start < 0:
        if optional:
            return ""
        raise AssertionError(f"install.sh is missing {name}")
    return text[start : text.index("\n}\n", start) + 3]


def _fake_plugin(tmp_path: Path, *, libraries: bool = True) -> Path:
    """A plugin inside a fake checkout, optionally with shared libraries.

    ``ssh-manager`` is installed explicitly (via the vendored-lib resolver),
    ``plugin-resolve`` only through the plugin's ``[tool.uv.sources]``, and
    ``agent-procutil`` only transitively through ``ssh-manager``'s own sources.
    """
    repo = tmp_path / "repo"
    plugin = repo / "plugins" / "plugin"
    (plugin / "src" / "pkg").mkdir(parents=True)
    (plugin / "pyproject.toml").write_text(
        f'[project]\nname = "pkg"\nversion = "{_VERSION}"\n\n'
        + (
            "[tool.uv.sources]\n"
            'agent-ssh-manager = { path = "../../libs/ssh-manager", editable = true }\n'
            'agent-plugin-resolve = { path = "../../libs/plugin-resolve", editable = true }\n'
            if libraries else ""
        ),
        encoding="utf-8",
    )
    (plugin / "src" / "pkg" / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (plugin / "README.md").write_text("# pkg\n", encoding="utf-8")
    if not libraries:
        return plugin
    for lib, sources in (
        ("ssh-manager", 'agent-procutil = { path = "../agent-procutil", editable = true }\n'),
        ("plugin-resolve", ""),
        ("agent-procutil", ""),
    ):
        module = lib.replace("-", "_")
        (repo / "libs" / lib / "src" / module).mkdir(parents=True)
        (repo / "libs" / lib / "pyproject.toml").write_text(
            f'[project]\nname = "agent-{lib}"\nversion = "0.1.0"\n'
            + (f"\n[tool.uv.sources]\n{sources}" if sources else ""),
            encoding="utf-8",
        )
        (repo / "libs" / lib / "src" / module / "__init__.py").write_text(
            "VALUE = 1\n", encoding="utf-8"
        )
    return plugin


def _lib_source(tmp_path: Path, lib: str) -> Path:
    module = lib.replace("-", "_")
    return tmp_path / "repo" / "libs" / lib / "src" / module / "__init__.py"


def _plugin_source(tmp_path: Path) -> Path:
    return tmp_path / "repo" / "plugins" / "plugin" / "src" / "pkg" / "__init__.py"


def _published_hash(tmp_path: Path) -> str:
    return json.loads(_slot_marker(tmp_path).read_text(encoding="utf-8"))["payload_hash"]


def _reported(output: str, key: str) -> str:
    for line in output.splitlines():
        if line.strip().startswith(f"{key}="):
            return line.strip()[len(key) + 1 :]
    raise AssertionError(f"{key}= not reported:\n{output}")


def _events(tmp_path: Path) -> list[str]:
    path = tmp_path / "events.txt"
    return path.read_text(encoding="utf-8").split() if path.exists() else []


def _slot_marker(tmp_path: Path) -> Path:
    return tmp_path / "install" / "versions" / _VERSION / _MARKER


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _assert_outcome(
    tmp_path: Path, result: subprocess.CompletedProcess, expected: str, *, failure_code: int = 42
) -> None:
    events = _events(tmp_path)
    detail = (
        f"rc={result.returncode}\nevents={events}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "START" in events, detail
    if expected == _PROCEED:
        assert result.returncode == failure_code, detail
        assert "PROCEEDED" in events, detail
        assert not (tmp_path / "install" / "versions" / _VERSION).exists(), detail
    elif expected == _NOOP:
        assert result.returncode == 0, detail
        assert "PROCEEDED" not in events and "DRAINED" not in events, detail
        assert "MANIFEST" in events, detail
        assert _tree(tmp_path / "install" / "versions" / _VERSION) == _tree(
            tmp_path / "before-slot"
        ), "same-content reconcile changed the completed slot"
    elif expected == _REUSED:
        # The activation stub exits 50: the slot was health-checked and
        # activated, but never rebuilt, reinstalled, or pruned.
        assert result.returncode == 50, detail
        assert "ACTIVATED" in events and "HEALTH" in events, detail
        for sentinel in ("PROCEEDED", "BUILT", "PRUNED", "DRAINED"):
            assert sentinel not in events, detail
        slot = tmp_path / "install" / "versions" / _VERSION
        assert _tree(slot) == _tree(tmp_path / "before-slot"), (
            "activating a reused completed slot changed its files"
        )
    else:
        assert result.returncode == 1, detail
        for sentinel in ("PROCEEDED", "DRAINED", "MARKER", "MANIFEST"):
            assert sentinel not in events, detail
        assert "bump the plugin version" in result.stdout + result.stderr, detail
        before = tmp_path / "before.json"
        assert before.exists(), detail
        assert _slot_marker(tmp_path).read_bytes() == before.read_bytes(), (
            "refusal changed the published completion evidence"
        )


# --------------------------------------------------------------------------
# PowerShell
# --------------------------------------------------------------------------

_PS_FUNCTIONS = (
    ("Get-UvSourcePaths", True),
    ("Get-PayloadLibRoots", True),
    ("Get-PayloadHash", False),
    ("Test-PayloadUnchangedSinceAdmission", True),
    ("Test-SlotContentCurrent", False),
    ("Get-SlotAdmission", True),
    ("Write-SlotAdmissionRefusal", True),
    ("Test-SlotIsLiveDifferentContent", True),
    ("Invoke-VersionedSlotClean", False),
    ("Invoke-VersionedMarkComplete", False),
    ("Invoke-InstallSlotBuild", True),
    ("Invoke-UpdateSlotBuild", True),
    ("Invoke-Install", False),
    ("Invoke-ServingRuntimeReconcile", False),
    ("Invoke-Update", False),
)

_PS_SETUP = {
    "absent": "Remove-Item -LiteralPath $VenvDir -Recurse -Force\n",
    "incomplete": "Set-Content -LiteralPath (Join-Path $VenvDir 'partial.txt') 'half-built'\n",
    "incomplete-active": "Set-Content -LiteralPath (Join-Path $VenvDir 'partial.txt') 'half-built'; Activate\n",
    "match-active": "Mark $ph; Activate\n",
    "match-inactive": "Mark $ph\n",
    "conflict-active": "Mark 'stale-payload-hash'; Activate\n",
    "conflict-inactive": "Mark 'stale-payload-hash'\n",
    "malformed": "Set-Content -LiteralPath (Join-Path $VenvDir '.install-complete.json') '{not json'\n",
}


def _ps_harness(
    tmp_path: Path, scenario: str, action: str, extra: tuple[str, ...] = (),
    *, libraries: bool = True,
) -> Path:
    text = _INSTALL_PS1.read_text(encoding="utf-8")
    functions = "\n\n".join(
        _extract_ps(name, text, optional=optional)
        for name, optional in (*_PS_FUNCTIONS, *((n, False) for n in extra))
    )
    body = f"""Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ScriptDir = {_ps_quote(_SCRIPTS)}
$PluginDir = {_ps_quote(_fake_plugin(tmp_path, libraries=libraries))}
$InstallDir = {_ps_quote(tmp_path / "install")}
$LinkDir = Join-Path $InstallDir 'venv'
$SrcVersion = '{_VERSION}'
$VersionedRuntime = $true
$VenvDir = Join-Path (Join-Path $InstallDir 'versions') $SrcVersion
$RealPython = {_ps_quote(sys.executable)}
# The slot interpreter is a function so `-m agent_bridge ...` (config migrate)
# is recorded instead of touching the real machine config; every
# versioned_runtime.py call resolves through $LinkPython / Get-BootstrapPython.
$VenvPython = 'FakeSlotPython'
$LinkPython = $RealPython
$LocalBin = {_ps_quote(tmp_path / "localbin")}
$Events = {_ps_quote(tmp_path / "events.txt")}
function Rec([string]$e) {{ Add-Content -LiteralPath $Events $e }}
function Write-Ok   {{ param([string]$Msg) Write-Host "  [OK]   $Msg" }}
function Write-Skip {{ param([string]$Msg) }}
function Write-Fail {{ param([string]$Msg) Write-Host "  [FAIL] $Msg" }}
function Write-Step {{ param([string]$Msg) }}
function Write-Warn {{ param([string]$Msg) Write-Host "  [WARN] $Msg" }}
function Ensure-Uv {{ return $true }}
function Invoke-MigrationCheck {{ }}
function Enter-InstallLock {{ return $true }}
function Exit-InstallLock {{ }}
function Get-BootstrapPython {{ return $RealPython }}
function FakeSlotPython {{
    if ($args -contains '--reconcile-serving') {{
        if ($args -contains '--probe-serving') {{
            Rec 'PROBE'
            $global:LASTEXITCODE = if ($env:SERVING_PROBE_EXIT_CODE) {{ [int]$env:SERVING_PROBE_EXIT_CODE }} else {{ 0 }}
            return
        }}
        Rec 'SERVING'
        if (-not (Test-Path (Join-Path $InstallDir 'update-in-progress'))) {{ throw 'Serving transition has no update marker' }}
        $global:LASTEXITCODE = if ($env:SERVING_EXIT_CODE) {{ [int]$env:SERVING_EXIT_CODE }} else {{ 0 }}
        return
    }}
    if ($args -contains 'agent_bridge') {{ Rec 'CONFIG'; $global:LASTEXITCODE = 0; return }}
    & $RealPython @args
}}
function Get-RunningProcess {{ return $null }}
function Write-DeployManifest {{ param([string]$PayloadHash = '') Rec 'MANIFEST'; Rec "MANIFEST_HASH=$PayloadHash" }}
# Hermetic shared-library resolution: only the fake checkout's libs/.
function Resolve-VendoredLib {{
    param([string]$LibName)
    $c = Join-Path $PluginDir "..\\..\\libs\\$LibName"
    if (Test-Path -LiteralPath (Join-Path $c 'pyproject.toml')) {{ return (Resolve-Path -LiteralPath $c).Path }}
    return $null
}}
function Write-UpdateMarker {{ Rec 'MARKER'; Set-Content -LiteralPath (Join-Path $InstallDir 'update-in-progress') -Value 'held' }}
function Invoke-UpdateDrainStop {{ Rec 'DRAINED'; exit 43 }}
function Test-ActiveIsForward {{ return $false }}
function Get-ActiveSignature {{ return '' }}
function Clear-UpdateMarker {{
    $path = Join-Path $InstallDir 'update-in-progress'
    if (Test-Path $path) {{ Remove-Item -LiteralPath $path; Rec 'CLEARED' }}
}}
function Remove-VenvBackup {{ }}
function Get-VersionedCurrent {{ return '' }}
function Install-SiblingPlugins {{ Rec 'PRUNED' }}
function Remove-ConsoleTrampolines {{ Rec 'PRUNED' }}
function Test-RuntimeHealthy {{ Rec 'HEALTH'; return $true }}
function Invoke-VersionedActivate {{ Rec 'ACTIVATED'; exit 50 }}
function New-SignedVenv {{ Rec 'PROCEEDED'; exit 42 }}
function New-SignedVenvShared {{ Rec 'BUILT'; return $true }}

{functions}

$vr = Join-Path $ScriptDir 'versioned_runtime.py'
$ph = Get-PayloadHash
if (-not $ph) {{ throw 'payload hash unavailable' }}
function Mark([string]$hash) {{
    & $RealPython $vr --root $InstallDir --link-name venv mark-complete $SrcVersion --payload-hash $hash | Out-Null
    if ($LASTEXITCODE -ne 0) {{ throw 'setup mark-complete failed' }}
}}
function Activate {{
    & $RealPython $vr --root $InstallDir --link-name venv activate $SrcVersion --no-link | Out-Null
    if ($LASTEXITCODE -ne 0) {{ throw 'setup activate failed' }}
}}
New-Item -ItemType Directory -Force -Path $VenvDir | Out-Null
{_PS_SETUP[scenario]}$m = Join-Path $VenvDir '.install-complete.json'
if (Test-Path -LiteralPath $m) {{ Copy-Item -LiteralPath $m {_ps_quote(tmp_path / "before.json")} }}
if (Test-Path -LiteralPath $VenvDir) {{ Copy-Item -LiteralPath $VenvDir {_ps_quote(tmp_path / "before-slot")} -Recurse }}
Rec 'START'
try {{
{action}
}} finally {{
    if (Test-Path Function:Stop-RuntimeConstruction) {{
        Stop-RuntimeConstruction
    }}
}}
"""
    harness = tmp_path / "harness.ps1"
    harness.write_text(body, encoding="utf-8")
    return harness


def _run_ps(harness: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_PWSH, "-NoProfile", "-NonInteractive", "-File", str(harness)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@pytest.mark.skipif(_PWSH is None, reason="pwsh is not available")
@pytest.mark.parametrize("action", ["Invoke-Update", "Invoke-Install"])
@pytest.mark.parametrize(("scenario", "expected"), _ADMISSION_CASES)
def test_ps1_completed_slot_admission(
    tmp_path: Path, action: str, scenario: str, expected: str
) -> None:
    result = _run_ps(_ps_harness(tmp_path, scenario, action, libraries=False))
    _assert_outcome(tmp_path, result, expected, failure_code=1)
    assert ("PROBE" in _events(tmp_path)) is (
        action == "Invoke-Update" and scenario == "match-active"
    )
    if expected == _NOOP:
        assert "MARKER" not in _events(tmp_path)


@pytest.mark.skipif(_PWSH is None, reason="pwsh is not available")
def test_ps1_harness_settles_a_defined_construction_function(tmp_path: Path) -> None:
    result = _run_ps(_ps_harness(
        tmp_path, "incomplete",
        "function Stop-RuntimeConstruction { Rec 'SETTLED' }\nRec 'ACTION'",
        libraries=False,
    ))
    assert result.returncode == 0, result.stdout + result.stderr
    assert _events(tmp_path) == ["START", "ACTION", "SETTLED"]


@pytest.mark.skipif(_PWSH is None, reason="pwsh is not available")
@pytest.mark.parametrize("after_wait", [False, True], ids=["selected", "selected-during-lock"])
def test_ps1_serving_reconcile_failure_never_publishes_or_rebuilds(tmp_path, after_wait):
    action = "$env:SERVING_PROBE_EXIT_CODE = '10'\n$env:SERVING_EXIT_CODE = '17'\n"
    if after_wait:
        action += "function Enter-InstallLock { Activate; return $true }\n"
    action += "Invoke-Update"
    result = _run_ps(_ps_harness(
        tmp_path, "match-inactive" if after_wait else "match-active", action,
        libraries=False,
    ))
    events = _events(tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "SERVING" in events
    assert events.count("MARKER") == events.count("CLEARED") == 1
    assert events.index("MARKER") < events.index("SERVING") < events.index("CLEARED")
    assert not set(events) & {"MANIFEST", "DRAINED", "PROCEEDED", "BUILT", "PRUNED"}
    assert _tree(tmp_path / "install" / "versions" / _VERSION) == _tree(
        tmp_path / "before-slot"
    )
    assert "Serving runtime reconcile failed" in result.stdout + result.stderr


@pytest.mark.skipif(_PWSH is None, reason="pwsh is not available")
def test_ps1_serving_transition_marker_is_cleared_after_success(tmp_path):
    result = _run_ps(_ps_harness(
        tmp_path, "match-active", "$env:SERVING_PROBE_EXIT_CODE = '10'\nInvoke-Update",
        libraries=False,
    ))
    _assert_outcome(tmp_path, result, _NOOP)
    events = _events(tmp_path)
    assert events.count("MARKER") == events.count("CLEARED") == 1
    assert events.index("MARKER") < events.index("SERVING") < events.index("CLEARED")
    assert not (tmp_path / "install" / "update-in-progress").exists()


# An unavailable payload hash must never let a same-content shortcut accept a
# completed slot on its marker alone: every entry point (pre-lock, post-lock,
# update) reaches the fail-closed `unverified` refusal instead.
_PS_NO_HASH = "function Get-PayloadHash { return '' }\n"
_PS_NO_HASH_ENTRIES = [
    # pre-lock shortcut: the slot is already active when the installer starts
    ("match-active", ""),
    # post-lock shortcut: a concurrent install activates it while we wait
    ("match-inactive", "function Enter-InstallLock { Activate; return $true }\n"),
]


@pytest.mark.skipif(_PWSH is None, reason="pwsh is not available")
@pytest.mark.parametrize("action", ["Invoke-Update", "Invoke-Install"])
@pytest.mark.parametrize(("scenario", "lock"), _PS_NO_HASH_ENTRIES, ids=["pre-lock", "post-lock"])
def test_ps1_unhashable_payload_refuses_active_completed_slot(
    tmp_path: Path, action: str, scenario: str, lock: str
) -> None:
    result = _run_ps(_ps_harness(tmp_path, scenario, _PS_NO_HASH + lock + action))
    _assert_outcome(tmp_path, result, _REFUSED)
    assert "(unverified)" in result.stdout, result.stdout + result.stderr


@pytest.mark.skipif(_PWSH is None, reason="pwsh is not available")
@pytest.mark.parametrize(
    ("scenario", "expected"),
    [("conflict-inactive", "False"), ("incomplete", "True"), ("match-inactive", "True")],
)
def test_ps1_mark_complete_reports_publication_result(
    tmp_path: Path, scenario: str, expected: str
) -> None:
    """A refused completion publication must surface as failure (#5472)."""
    harness = _ps_harness(
        tmp_path,
        scenario,
        "[void](Invoke-VersionedSlotClean -PayloadHash $ph)\n"
        "$r = Invoke-VersionedMarkComplete -PayloadHash $ph\nWrite-Host \"RESULT=[$r]\"\n",
    )
    result = _run_ps(harness)
    assert result.returncode == (1 if expected == "False" else 0), result.stdout + result.stderr
    if expected != "False":
        assert f"RESULT=[{expected}]" in result.stdout, result.stdout + result.stderr
    assert _slot_marker(tmp_path).exists()
    if scenario == "conflict-inactive":
        assert _slot_marker(tmp_path).read_bytes() == (tmp_path / "before.json").read_bytes()


def test_ps1_call_sites_fail_on_refused_completion_and_guard_precedes_mutation() -> None:
    text = _INSTALL_PS1.read_text(encoding="utf-8")
    assert (
        text.count(
            "if (-not (Invoke-VersionedMarkComplete -PayloadHash $payloadHash)) { throw"
        )
        == 2
    )
    for name, mutations in (
        ("Invoke-Update", ("Write-UpdateMarker\n", "Invoke-UpdateDrainStop -", "Invoke-UpdateSlotBuild -")),
        ("Invoke-Install", ("Invoke-InstallSlotBuild\n",)),
    ):
        body = _extract_ps(name, text)
        refusal = body.index("exit 1", body.index("$admission = Get-SlotAdmission"))
        assert refusal < min(body.index(m) for m in mutations), name
        # Post-build source verification precedes every publication/activation.
        verify = body.index("Test-PayloadUnchangedSinceAdmission -AdmittedHash $payloadHash")
        assert verify < min(
            body.index("Invoke-VersionedMarkComplete -"),
            body.index("Invoke-VersionedActivate"),
            body.rindex("Write-DeployManifest -PayloadHash $payloadHash"),
        ), name


# --------------------------------------------------------------------------
# The payload fingerprint covers every install source and is computed once
# --------------------------------------------------------------------------

# Shared libraries the installer installs from outside the plugin directory.
_SHARED_LIBS = (
    "ssh-manager",  # installed explicitly from the resolved lib
    "plugin-resolve",  # installed via the plugin's [tool.uv.sources]
    "agent-procutil",  # installed transitively via ssh-manager's own sources
)

# Files the build and local test runs write into a shared library's checkout,
# none of which is ever installed into the slot.
_LIB_ARTIFACTS = (
    "build/lib/ssh_manager/__init__.py",
    "src/agent_ssh_manager.egg-info/PKG-INFO",
    "src/ssh_manager/__pycache__/__init__.cpython-312.pyc",
    "tests/test_ssh_manager.py",
    ".ruff_cache/content",
)


def _assert_hash_steps(result: subprocess.CompletedProcess) -> None:
    """H0 (baseline) == H1 (after build/test artifacts), then every shared-lib
    source edit (H-<lib>) changes the hash again."""
    detail = result.stdout + result.stderr
    previous = _reported(result.stdout, "H0")
    assert previous, detail
    assert _reported(result.stdout, "H1") == previous, "build/test artifacts changed the hash\n" + detail
    for lib in _SHARED_LIBS:
        current = _reported(result.stdout, f"H-{lib}")
        assert current != previous, f"a {lib} source change was not hashed\n" + detail
        previous = current


@pytest.mark.skipif(_PWSH is None, reason="pwsh is not available")
def test_ps1_shared_lib_only_change_is_not_reused(tmp_path: Path) -> None:
    """A shared library the installer installs is part of the slot's content:
    changing only its source must refuse the completed active slot, never
    reuse it as unchanged (#5472)."""
    change = f"Add-Content -LiteralPath {_ps_quote(_lib_source(tmp_path, 'ssh-manager'))} 'VALUE = 2'\n"
    result = _run_ps(_ps_harness(tmp_path, "match-active", change + "Invoke-Install"))
    _assert_outcome(tmp_path, result, _REFUSED)
    assert "different payload content" in result.stdout, result.stdout + result.stderr


@pytest.mark.skipif(_PWSH is None, reason="pwsh is not available")
def test_ps1_readme_only_change_is_not_reused(tmp_path: Path) -> None:
    """The plugin README is the declared project ``readme``; setuptools embeds
    it in the installed metadata, so changing only it must refuse the slot."""
    readme = _plugin_source(tmp_path).parents[2] / "README.md"
    change = f"Add-Content -LiteralPath {_ps_quote(readme)} 'more'\n"
    result = _run_ps(_ps_harness(tmp_path, "match-active", change + "Invoke-Install"))
    _assert_outcome(tmp_path, result, _REFUSED)
    assert "different payload content" in result.stdout, result.stdout + result.stderr


@pytest.mark.skipif(_PWSH is None, reason="pwsh is not available")
def test_ps1_payload_hash_covers_every_shared_lib_but_not_artifacts(tmp_path: Path) -> None:
    lib_dir = tmp_path / "repo" / "libs" / "ssh-manager"
    action = "Write-Host \"H0=$(Get-PayloadHash)\"\n"
    action += "".join(
        f"New-Item -ItemType File -Force -Path {_ps_quote(lib_dir / rel)} -Value 'x' | Out-Null\n"
        for rel in _LIB_ARTIFACTS
    )
    action += "Write-Host \"H1=$(Get-PayloadHash)\"\n"
    for lib in _SHARED_LIBS:
        action += f"Set-Content -LiteralPath {_ps_quote(_lib_source(tmp_path, lib))} 'VALUE = 2'\n"
        action += f"Write-Host \"H-{lib}=$(Get-PayloadHash)\"\n"
    _assert_hash_steps(_run_ps(_ps_harness(tmp_path, "absent", action)))


_EDIT_MID_BUILD = (False, True)
_MID_BUILD_IDS = ["sources-unchanged", "sources-edited-mid-build"]


def _assert_mid_build_outcome(
    tmp_path: Path, result: subprocess.CompletedProcess, *, edited: bool, update: bool
) -> None:
    """Unchanged sources publish the admitted hash and activate; sources
    edited after admission but before publication refuse the install: no
    completion marker, no deploy manifest, no activation (#5472)."""
    events = _events(tmp_path)
    output = result.stdout + result.stderr
    detail = f"rc={result.returncode}\nevents={events}\n{output}"
    assert "BUILT" in events, detail
    ph = _reported(result.stdout, "PH")
    if not edited:
        assert result.returncode == 50 and "ACTIVATED" in events, detail
        assert _published_hash(tmp_path) == ph, detail
        return
    assert result.returncode == 1, detail
    assert "changed during the build" in output and "re-run the install" in output, detail
    assert not _slot_marker(tmp_path).exists(), detail
    for sentinel in ("ACTIVATED", "MANIFEST"):
        assert sentinel not in events, detail
    slot = tmp_path / "install" / "versions" / _VERSION
    assert not slot.exists(), detail


@pytest.mark.skipif(_PWSH is None, reason="pwsh is not available")
@pytest.mark.parametrize("edited", _EDIT_MID_BUILD, ids=_MID_BUILD_IDS)
@pytest.mark.parametrize("action", ["Invoke-Install", "Invoke-Update"])
def test_ps1_marker_is_published_only_for_the_bytes_admission_hashed(
    tmp_path: Path, action: str, edited: bool
) -> None:
    """A source edited after admission but while the slot is being built
    must not be published under the admitted hash."""
    source = _ps_quote(_plugin_source(tmp_path))
    edit = f"; Add-Content -LiteralPath {source} 'VALUE = 2'" if edited else ""
    build = "Invoke-InstallSlotBuild" if action == "Invoke-Install" else "Invoke-UpdateSlotBuild"
    harness = _ps_harness(
        tmp_path,
        "incomplete",
        "Write-Host \"PH=$ph\"\n"
        f"function {build} {{ param($UseCutover) Rec 'BUILT'{edit} }}\n"
        f"{action}\n",
    )
    result = _run_ps(harness)
    _assert_mid_build_outcome(tmp_path, result, edited=edited, update=action == "Invoke-Update")


@pytest.mark.skipif(_PWSH is None, reason="pwsh is not available")
def test_ps1_manifest_records_the_admitted_hash(tmp_path: Path) -> None:
    """The deploy manifest records the hash admission matched, not one
    recomputed after a source change landed mid-transaction."""
    source = _ps_quote(_plugin_source(tmp_path))
    action = (
        "Write-Host \"PH=$ph\"\n"
        # A concurrent install activates the slot while we wait for the lock.
        "function Enter-InstallLock { Activate; return $true }\n"
        # Runs inside admission, after the transaction's hash was taken.
        "function Get-BootstrapPython {\n"
        f"    Add-Content -LiteralPath {source} 'VALUE = 2'; return $RealPython\n"
        "}\n"
        "Invoke-Install\n"
    )
    result = _run_ps(_ps_harness(tmp_path, "match-inactive", action))
    detail = f"rc={result.returncode}\nevents={_events(tmp_path)}\n{result.stdout}{result.stderr}"
    assert result.returncode == 0, detail
    assert f"MANIFEST_HASH={_reported(result.stdout, 'PH')}" in _events(tmp_path), detail


_WINDOWS_POWERSHELL = shutil.which("powershell") if os.name == "nt" else None


@pytest.mark.skipif(
    _PWSH is None or _WINDOWS_POWERSHELL is None,
    reason="needs both pwsh and Windows PowerShell 5.1",
)
def test_ps1_payload_hash_is_identical_on_both_powershell_hosts(tmp_path: Path) -> None:
    """Either host may run the installer against the same slot, so the hash
    must not depend on the host's culture-aware sort order (NLS vs ICU)."""
    harness = _ps_harness(tmp_path, "absent", "Write-Host \"PH=$ph\"\n")
    for name in ("a-b.py", "a_b.py", "a.b.py", "ab.py", "aB.py"):
        (_plugin_source(tmp_path).parent / name).write_text("X = 1\n", encoding="utf-8")
    hashes = {}
    for host in (_PWSH, _WINDOWS_POWERSHELL):
        result = subprocess.run(
            [host, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        hashes[host] = _reported(result.stdout, "PH")
        assert hashes[host], result.stdout + result.stderr
    assert len(set(hashes.values())) == 1, hashes


# --------------------------------------------------------------------------
# POSIX (bash)
# --------------------------------------------------------------------------

_SH_FUNCTIONS = (
    ("_uv_source_paths", True),
    ("_payload_lib_roots", True),
    ("_payload_tree_digests", True),
    ("_compute_payload_hash", True),
    ("_payload_hash", False),
    ("_payload_unchanged_since_admission", True),
    ("_bootstrap_python", False),
    ("_versioned_current", False),
    ("_versioned_slot_clean", False),
    ("_versioned_mark_complete", False),
    ("_slot_admission", True),
    ("_slot_admission_refusal", True),
    ("_guard_published_slot", True),
    ("_install_slot_build", True),
    ("_install_lock_timeout", True),
    ("_exit_install_lock", True),
    ("_lifecycle_emit", False),
    ("_lifecycle_finish", False),
    ("_require_install_lock", True),
    ("_update_core", False),
    ("do_install", False),
    ("_reconcile_serving_runtime", False),
    ("do_update", False),
)

_SH_SETUP = {
    "absent": 'rm -rf "$VENV_DIR"\n',
    "incomplete": 'echo half-built > "$VENV_DIR/partial.txt"\n',
    "incomplete-active": 'echo half-built > "$VENV_DIR/partial.txt"; activate\n',
    "match-active": 'slot_python; mark "$ph"; activate\n',
    "match-inactive": 'slot_python; mark "$ph"\n',
    "conflict-active": "slot_python; mark stale-payload-hash; activate\n",
    "conflict-inactive": "slot_python; mark stale-payload-hash\n",
    "malformed": "slot_python; printf '{not json' > \"$VENV_DIR/.install-complete.json\"\n",
}


@pytest.mark.skipif(_bash() is None or os.name == "nt", reason="POSIX bash required")
@pytest.mark.parametrize("action", ["do_install", "do_update"])
@pytest.mark.parametrize("scenario", ["match-active", "absent"])
def test_actual_installer_admission_records_lifecycle_outcome(
    tmp_path: Path, action: str, scenario: str
) -> None:
    lifecycle_action = action.removeprefix("do_")
    script = _sh_harness(
        tmp_path, scenario,
        f'_LIFECYCLE_ACTION={lifecycle_action}\n'
        'trap \'_lifecycle_finish "$?"\' EXIT\n'
        f'_lifecycle_emit begin\n{action}',
    )
    recorder = tmp_path / "repo" / "plugins" / "plugin" / "libs" / "zdd" / "src" / "zdd" / "lifecycle.py"
    recorder.parent.mkdir(parents=True)
    canonical = _PLUGIN_ROOT.parents[1] / "libs" / "zdd" / "src" / "zdd" / "lifecycle.py"
    shutil.copyfile(canonical, recorder)
    result = _run_sh(script)
    expected = _NOOP if scenario == "match-active" else _PROCEED
    _assert_outcome(tmp_path, result, expected)
    events = [
        json.loads(line)
        for line in (tmp_path / "install" / "lifecycle.log").read_text(encoding="utf-8").splitlines()
    ]
    assert [(event["action"], event["outcome"]) for event in events] == [
        (lifecycle_action, "begin"),
        (lifecycle_action, "ok" if scenario == "match-active" else "fail"),
    ]


def _sh_runtime_scripts(tmp_path: Path) -> Path:
    if os.name != "nt":
        return _SCRIPTS
    scripts = tmp_path / "runtime-scripts"
    scripts.mkdir()
    for name in (
        "versioned_runtime.py", "versioned_runtime_leases.py", "versioned_runtime_construction.py",
        "versioned_runtime_source.py",
        "versioned_runtime_publication.py",
    ):
        shutil.copyfile(_SCRIPTS / name, scripts / name)
    core = scripts / "versioned_runtime.py"
    source = core.read_text(encoding="utf-8")
    entrypoint = 'if __name__ == "__main__":\n'
    assert source.count(entrypoint) == 1
    core.write_text(
        source.replace(entrypoint, entrypoint + '    sys.stdout.reconfigure(newline="\\n")\n'),
        encoding="utf-8",
    )
    # Git Bash's FIFO is not a native Windows Python stdin handle. Model the
    # POSIX transport with an anonymous pipe, retaining the real lease/protocol.
    component = scripts / "versioned_runtime_construction.py"
    adapter = component.read_text(encoding="utf-8")
    assert adapter.count('        exec "$@"\n') == 1
    component.write_text(
        adapter.replace('        exec "$@"\n', '''
        exec 7< <(cat)
        relay=$!
        rc=0
        "$@" <&7 || rc=$?
        kill "$relay" 2>/dev/null || true
        wait "$relay" 2>/dev/null || true
        exit "$rc"
'''), encoding="utf-8",
    )
    return scripts


def _sh_harness(
    tmp_path: Path, scenario: str, action: str, extra: tuple[str, ...] = (),
    *, libraries: bool = True,
) -> Path:
    text = _INSTALL_SH.read_text(encoding="utf-8")
    functions = "".join(
        _extract_sh(name, text, optional=optional)
        for name, optional in (*_SH_FUNCTIONS, *((n, False) for n in extra))
    )
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    # `-m agent_bridge ...` (config migrate) is recorded, never run against
    # the real machine config; everything else is the real interpreter.
    shim = (
        "#!/bin/sh\n"
        'case "$*" in *--probe-serving*) '
        f"echo PROBE >> {_sh_quote(_posix(tmp_path / 'events.txt'))}; "
        'exit "${SERVING_PROBE_EXIT_CODE:-0}" ;; esac\n'
        'case "$*" in *--reconcile-serving*) '
        f"echo SERVING >> {_sh_quote(_posix(tmp_path / 'events.txt'))}; "
        f"test -f {_sh_quote(_posix(tmp_path / 'install' / 'update-in-progress'))} || exit 18; "
        'exit "${SERVING_EXIT_CODE:-0}" ;; esac\n'
        'case "$*" in *agent_bridge*) '
        f"echo CONFIG >> {_sh_quote(_posix(tmp_path / 'events.txt'))}; exit 0 ;; esac\n"
        f"exec {_sh_quote(_posix(sys.executable))} \"$@\"\n"
    )
    for name in ("python3", "python"):
        path = shim_dir / name
        path.write_text(shim, encoding="utf-8", newline="\n")
        path.chmod(0o755)
    plugin = _fake_plugin(tmp_path, libraries=libraries)
    script = f"""set -euo pipefail
SCRIPT_DIR={_sh_quote(_posix(_sh_runtime_scripts(tmp_path)))}
PLUGIN_DIR={_sh_quote(_posix(plugin))}
INSTALL_DIR={_sh_quote(_posix(tmp_path / "install"))}
LINK_DIR="$INSTALL_DIR/venv"
SRC_VERSION={_VERSION}
VENV_DIR="$INSTALL_DIR/versions/$SRC_VERSION"
VERSIONED_RUNTIME=1
PUBLISH_GLOBAL_BINSTUBS=false
FORCE=true
EVENTS={_sh_quote(_posix(tmp_path / "events.txt"))}
SHIM={_sh_quote(_posix(shim_dir / "python3"))}
export PATH={_sh_quote(_posix(shim_dir))}:"$PATH"
rec() {{ echo "$1" >> "$EVENTS"; }}
_ok() {{ echo "  [OK]   $*"; }}
_skip() {{ :; }}
_fail() {{ echo "  [FAIL] $*" >&2; }}
_step() {{ :; }}
_warn() {{ echo "  [WARN] $*" >&2; }}
_ensure_uv() {{ return 0; }}
_ensure_uv_index() {{ :; }}
_downgrade_guard() {{ :; }}
_migration_check() {{ :; }}
_enter_install_lock() {{ return 0; }}
_write_deploy_manifest() {{ rec MANIFEST; }}
_write_update_marker() {{ rec MARKER; echo held > "$INSTALL_DIR/update-in-progress"; }}
_clear_update_marker() {{
    if [[ -f "$INSTALL_DIR/update-in-progress" ]]; then
        rm "$INSTALL_DIR/update-in-progress"; rec CLEARED
    fi
}}
_update_lifecycle_drain_stop() {{ rec DRAINED; exit 43; }}
_active_is_forward() {{ return 1; }}
_active_signature() {{ :; }}
_get_pid() {{ return 1; }}
SYSTEMD_UNIT=agent-bridge-admission-test.service
_install_sibling_plugins() {{ rec PRUNED; }}
_runtime_healthy() {{ rec HEALTH; return 0; }}
_versioned_activate() {{ rec ACTIVATED; exit 50; }}
_venv_healthy() {{ return 1; }}
_uv_venv_resilient() {{ rec PROCEEDED; rec BUILT; exit 42; }}
# Hermetic shared-library resolution: only the fake checkout's libs/.
_resolve_vendored_lib() {{
    local c="$PLUGIN_DIR/../../libs/$1"
    [[ -f "$c/pyproject.toml" ]] || return 1
    (cd "$c" && pwd)
}}
{functions}
vr="$SCRIPT_DIR/versioned_runtime.py"
ph="$(_payload_hash)"
[[ -n "$ph" ]] || {{ echo 'payload hash unavailable' >&2; exit 90; }}
slot_python() {{ mkdir -p "$VENV_DIR/bin"; cp "$SHIM" "$VENV_DIR/bin/python"; chmod +x "$VENV_DIR/bin/python"; }}
mark() {{ "$SHIM" "$vr" --root "$INSTALL_DIR" --link-name venv mark-complete "$SRC_VERSION" --payload-hash "$1" >/dev/null; }}
activate() {{ "$SHIM" "$vr" --root "$INSTALL_DIR" --link-name venv activate "$SRC_VERSION" --no-link >/dev/null; }}
mkdir -p "$VENV_DIR"
{_SH_SETUP[scenario]}if [[ -e "$VENV_DIR/.install-complete.json" ]]; then cp "$VENV_DIR/.install-complete.json" {_sh_quote(_posix(tmp_path / "before.json"))}; fi
if [[ -d "$VENV_DIR" ]]; then cp -R "$VENV_DIR" {_sh_quote(_posix(tmp_path / "before-slot"))}; fi
rec START
{action}
"""
    path = tmp_path / "harness.sh"
    path.write_text(script, encoding="utf-8", newline="\n")
    return path


def _run_sh(harness: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_bash(), str(harness)],
        capture_output=True,
        text=True,
        timeout=_SH_TIMEOUT_SECONDS,
        check=False,
    )


@pytest.mark.timeout(_SH_TIMEOUT_SECONDS)
@pytest.mark.skipif(_bash() is None, reason="a conformant bash is not available")
@pytest.mark.parametrize("action", ["do_update", "do_install"])
@pytest.mark.parametrize(("scenario", "expected"), _ADMISSION_CASES)
def test_sh_completed_slot_admission(
    tmp_path: Path, action: str, scenario: str, expected: str
) -> None:
    # Transitive library hashing has dedicated native cases below.
    result = _run_sh(_sh_harness(tmp_path, scenario, action, libraries=False))
    _assert_outcome(tmp_path, result, expected)
    assert ("PROBE" in _events(tmp_path)) is (
        action == "do_update" and scenario == "match-active"
    )
    if expected == _NOOP:
        assert "MARKER" not in _events(tmp_path)


@pytest.mark.skipif(_bash() is None, reason="a conformant bash is not available")
@pytest.mark.parametrize("after_wait", [False, True], ids=["selected", "selected-during-lock"])
def test_sh_serving_reconcile_failure_never_publishes_or_rebuilds(tmp_path, after_wait):
    action = "export SERVING_PROBE_EXIT_CODE=10 SERVING_EXIT_CODE=17\n"
    if after_wait:
        action += "_enter_install_lock() { activate; return 0; }\n"
    action += "do_update"
    result = _run_sh(_sh_harness(
        tmp_path, "match-inactive" if after_wait else "match-active", action,
        libraries=False,
    ))
    events = _events(tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "SERVING" in events
    assert events.count("MARKER") == events.count("CLEARED") == 1
    assert events.index("MARKER") < events.index("SERVING") < events.index("CLEARED")
    assert not set(events) & {"MANIFEST", "DRAINED", "PROCEEDED", "BUILT", "PRUNED"}
    assert _tree(tmp_path / "install" / "versions" / _VERSION) == _tree(
        tmp_path / "before-slot"
    )
    assert "Serving runtime reconcile failed" in result.stdout + result.stderr


@pytest.mark.skipif(_bash() is None, reason="a conformant bash is not available")
def test_sh_serving_transition_marker_is_cleared_after_success(tmp_path):
    result = _run_sh(_sh_harness(
        tmp_path, "match-active", "export SERVING_PROBE_EXIT_CODE=10\ndo_update",
        libraries=False,
    ))
    _assert_outcome(tmp_path, result, _NOOP)
    events = _events(tmp_path)
    assert events.count("MARKER") == events.count("CLEARED") == 1
    assert events.index("MARKER") < events.index("SERVING") < events.index("CLEARED")
    assert not (tmp_path / "install" / "update-in-progress").exists()


_SH_NO_HASH = "_payload_hash() { :; }\n"
_SH_NO_HASH_ENTRIES = [
    ("match-active", ""),
    ("match-inactive", "_enter_install_lock() { activate; return 0; }\n"),
]


@pytest.mark.timeout(_SH_TIMEOUT_SECONDS)
@pytest.mark.skipif(_bash() is None, reason="a conformant bash is not available")
@pytest.mark.parametrize("action", ["do_update", "do_install"])
@pytest.mark.parametrize(("scenario", "lock"), _SH_NO_HASH_ENTRIES, ids=["pre-lock", "post-lock"])
def test_sh_unhashable_payload_refuses_active_completed_slot(
    tmp_path: Path, action: str, scenario: str, lock: str
) -> None:
    result = _run_sh(_sh_harness(tmp_path, scenario, _SH_NO_HASH + lock + action))
    _assert_outcome(tmp_path, result, _REFUSED)
    assert "(unverified)" in result.stderr, result.stdout + result.stderr


@pytest.mark.timeout(_SH_TIMEOUT_SECONDS)
@pytest.mark.skipif(_bash() is None, reason="a conformant bash is not available")
@pytest.mark.parametrize(
    ("scenario", "expected"),
    [("conflict-inactive", "fail"), ("incomplete", "ok"), ("match-inactive", "ok")],
)
def test_sh_mark_complete_reports_publication_result(
    tmp_path: Path, scenario: str, expected: str
) -> None:
    """A refused completion publication must surface as failure (#5472)."""
    prefix = "slot_python\n" if scenario == "incomplete" else ""
    harness = _sh_harness(
        tmp_path,
        scenario,
        prefix + 'if _versioned_slot_clean && _versioned_mark_complete; then echo "MC=ok"; else echo "MC=fail"; fi\n',
    )
    result = _run_sh(harness)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"MC={expected}" in result.stdout, result.stdout + result.stderr
    assert _slot_marker(tmp_path).exists()
    if scenario == "conflict-inactive":
        assert _slot_marker(tmp_path).read_bytes() == (tmp_path / "before.json").read_bytes()


def test_sh_call_sites_fail_on_refused_completion_and_guard_precedes_mutation() -> None:
    text = _INSTALL_SH.read_text(encoding="utf-8")
    assert "_versioned_mark_complete || exit 1\n" in text
    assert "|| true" not in _extract_sh("_versioned_mark_complete", text)
    update = _extract_sh("do_update", text)
    guard = update.index("if _guard_published_slot; then")
    assert guard < update.index("_write_update_marker\n")
    assert guard < update.index('_update_lifecycle_drain_stop "')
    completion_match = re.search(
        r"(?ms)^    _update_complete_candidate\(\) \{.*?^    \}", update,
    )
    assert completion_match is not None
    completion = completion_match.group()
    assert "_payload_unchanged_since_admission source-only || return $?" in completion
    assert "_versioned_mark_complete || return $?" in completion
    install = _extract_sh("do_install", text)
    assert install.index("if _guard_published_slot; then") < install.index("_install_slot_build\n")
    # Post-build source verification precedes every publication/activation.
    assert install.index("    _payload_unchanged_since_admission || exit 1\n") < min(
        install.index("_versioned_mark_complete ||"),
        install.index("_versioned_activate ||"),
        install.rindex("_write_deploy_manifest\n"),
    )
    verify = "if ! _update_core || ! _payload_unchanged_since_admission || ! _update_complete_candidate; then"
    assert update.index(verify) < min(
        update.index("_versioned_activate ||"), update.rindex("_write_deploy_manifest\n"),
    )


@pytest.mark.timeout(_SH_TIMEOUT_SECONDS)
@pytest.mark.skipif(_bash() is None, reason="a conformant bash is not available")
def test_sh_payload_hash_does_not_depend_on_locale(tmp_path: Path) -> None:
    """Identical-content files tie on their digest and are ordered by path;
    that order must not follow the caller's locale (shell vs service manager)."""
    harness = _sh_harness(tmp_path, "absent", 'echo "PH=$ph"\n')
    for name in ("a-b.py", "a_b.py", "aB.py", "ab.py"):
        (_plugin_source(tmp_path).parent / name).write_text("", encoding="utf-8")
    hashes = set()
    for locale in ("C", "en_US.UTF-8"):
        env = {**os.environ, "LC_ALL": locale}
        result = subprocess.run(
            [_bash(), str(harness)], capture_output=True, text=True, timeout=120, check=False, env=env
        )
        hashes.add(_reported(result.stdout, "PH"))
    assert len(hashes) == 1 and "" not in hashes, hashes


@pytest.mark.timeout(_SH_TIMEOUT_SECONDS)
@pytest.mark.skipif(_bash() is None, reason="a conformant bash is not available")
def test_sh_shared_lib_only_change_is_not_reused(tmp_path: Path) -> None:
    change = f"echo 'VALUE = 2' >> {_sh_quote(_posix(_lib_source(tmp_path, 'ssh-manager')))}\n"
    result = _run_sh(_sh_harness(tmp_path, "match-active", change + "do_install"))
    _assert_outcome(tmp_path, result, _REFUSED)
    assert "different payload content" in result.stderr, result.stdout + result.stderr


@pytest.mark.timeout(_SH_TIMEOUT_SECONDS)
@pytest.mark.skipif(_bash() is None, reason="a conformant bash is not available")
def test_sh_readme_only_change_is_not_reused(tmp_path: Path) -> None:
    readme = _plugin_source(tmp_path).parents[2] / "README.md"
    change = f"echo more >> {_sh_quote(_posix(readme))}\n"
    result = _run_sh(_sh_harness(tmp_path, "match-active", change + "do_install"))
    _assert_outcome(tmp_path, result, _REFUSED)
    assert "different payload content" in result.stderr, result.stdout + result.stderr


@pytest.mark.timeout(_SH_TIMEOUT_SECONDS)
@pytest.mark.skipif(_bash() is None, reason="a conformant bash is not available")
def test_sh_payload_hash_covers_every_shared_lib_but_not_artifacts(tmp_path: Path) -> None:
    lib_dir = tmp_path / "repo" / "libs" / "ssh-manager"
    action = 'echo "H0=$(_payload_hash)"\n'
    action += "".join(
        f"mkdir -p {_sh_quote(_posix((lib_dir / rel).parent))}; "
        f"echo x > {_sh_quote(_posix(lib_dir / rel))}\n"
        for rel in _LIB_ARTIFACTS
    )
    action += 'echo "H1=$(_payload_hash)"\n'
    for lib in _SHARED_LIBS:
        action += f"echo 'VALUE = 2' > {_sh_quote(_posix(_lib_source(tmp_path, lib)))}\n"
        action += f'echo "H-{lib}=$(_payload_hash)"\n'
    _assert_hash_steps(_run_sh(_sh_harness(tmp_path, "absent", action)))


@pytest.mark.timeout(_SH_TIMEOUT_SECONDS)
@pytest.mark.skipif(_bash() is None, reason="a conformant bash is not available")
@pytest.mark.parametrize(
    ("action", "edited"),
    # The unchanged-sources check is shared by both entry points and a bash
    # do_update run costs ~12s, so only install covers the unchanged case.
    [("do_install", False), ("do_install", True), ("do_update", True)],
    ids=["do_install-sources-unchanged", "do_install-sources-edited-mid-build", "do_update-sources-edited-mid-build"],
)
def test_sh_marker_is_published_only_for_the_bytes_admission_hashed(
    tmp_path: Path, action: str, edited: bool
) -> None:
    """A source edited after admission but while the slot is being built
    must not be published under the admitted (frozen) hash."""
    source = _sh_quote(_posix(_plugin_source(tmp_path)))
    edit = f" echo 'VALUE = 2' >> {source};" if edited else ""
    build = "_install_slot_build" if action == "do_install" else "_update_core"
    harness = _sh_harness(
        tmp_path,
        "incomplete",
        'echo "PH=$ph"\n'
        f"{build}() {{ rec BUILT;{edit} return 0; }}\n"
        f"{action}\n",
    )
    result = _run_sh(harness)
    _assert_mid_build_outcome(tmp_path, result, edited=edited, update=action == "do_update")


@pytest.mark.timeout(_SH_TIMEOUT_SECONDS)
@pytest.mark.skipif(_bash() is None, reason="a conformant bash is not available")
def test_sh_manifest_hash_is_the_admitted_hash(tmp_path: Path) -> None:
    """installer-engine's write_deploy_manifest reads `_payload_hash`; after
    admission it must return the admitted value, not a recomputation."""
    source = _sh_quote(_posix(_plugin_source(tmp_path)))
    action = (
        'echo "PH=$ph"\n'
        "if _guard_published_slot; then rec GUARDED; fi\n"
        f"echo 'VALUE = 2' >> {source}\n"
        'echo "MANIFEST_HASH=$(_payload_hash)"\n'
    )
    result = _run_sh(_sh_harness(tmp_path, "match-active", action))
    detail = f"rc={result.returncode}\n{result.stdout}{result.stderr}"
    assert result.returncode == 0 and "GUARDED" in _events(tmp_path), detail
    assert _reported(result.stdout, "MANIFEST_HASH") == _reported(result.stdout, "PH"), detail


# --------------------------------------------------------------------------
# Slot preparation refusal (same class: a swallowed helper refusal)
# --------------------------------------------------------------------------


@pytest.mark.skipif(_PWSH is None, reason="pwsh is not available")
@pytest.mark.parametrize(
    ("scenario", "built"), [("incomplete", True), ("incomplete-active", False)]
)
def test_ps1_refused_slot_preparation_never_builds_over_the_slot(
    tmp_path: Path, scenario: str, built: bool
) -> None:
    """`slot --clean-incomplete` refusing a selected incomplete slot must stop
    the build instead of letting `uv venv --allow-existing` rewrite it."""
    harness = _ps_harness(
        tmp_path,
        scenario,
        "[void](Invoke-VersionedSlotClean -PayloadHash $ph)\n"
        "$r = New-SignedVenv\nWrite-Host \"RESULT=[$r]\"\n",
        extra=("New-SignedVenv",),
    )
    result = _run_ps(harness)
    detail = result.stdout + result.stderr
    assert result.returncode == 1, detail
    assert ("BUILT" in _events(tmp_path)) is built, detail
    if built:
        assert "RESULT=[True]" in result.stdout, detail
    assert (tmp_path / "install" / "versions" / _VERSION / "partial.txt").exists() is not built


@pytest.mark.timeout(_SH_TIMEOUT_SECONDS)
@pytest.mark.skipif(_bash() is None, reason="a conformant bash is not available")
@pytest.mark.parametrize(
    ("scenario", "built"), [("incomplete", True), ("incomplete-active", False)]
)
def test_sh_refused_slot_preparation_never_builds_over_the_slot(
    tmp_path: Path, scenario: str, built: bool
) -> None:
    harness = _sh_harness(tmp_path, scenario, "do_install")
    result = _run_sh(harness)
    detail = f"rc={result.returncode}\n{result.stdout}{result.stderr}"
    assert ("BUILT" in _events(tmp_path)) is built, detail
    assert result.returncode == (42 if built else 1), detail
    assert (tmp_path / "install" / "versions" / _VERSION / "partial.txt").exists() is not built


def test_slot_preparation_refusal_is_propagated_by_every_caller() -> None:
    ps = _INSTALL_PS1.read_text(encoding="utf-8")
    for name in ("Invoke-Install", "Invoke-Update"):
        body = _extract_ps(name, ps)
        assert body.index("Invoke-VersionedSlotClean -PayloadHash $payloadHash") < body.index(
            "Invoke-InstallSlotBuild" if name == "Invoke-Install" else "Write-UpdateMarker"
        )
    sh = _INSTALL_SH.read_text(encoding="utf-8")
    assert "|| true" not in _extract_sh("_versioned_slot_clean", sh)
    for name in ("do_install", "do_update"):
        body = _extract_sh(name, sh)
        assert "_versioned_slot_clean || exit 1\n" in body
        boundary = "_install_slot_build\n" if name == "do_install" else '_update_lifecycle_drain_stop "'
        assert body.index("_versioned_slot_clean || exit 1") < body.index(boundary)
    update = _extract_sh("do_update", sh)
    assert update.index('trap \'_lifecycle_finish "$?"; _clear_update_marker; _exit_install_lock\' EXIT') < update.index(
        "_versioned_slot_clean || exit 1"
    )

def test_reused_slot_skips_every_slot_mutation_in_both_installers() -> None:
    """A matching completed slot is activated as-is (#5472): the build,
    package install and provider pruning are all behind the reuse branch."""
    ps = _INSTALL_PS1.read_text(encoding="utf-8")
    install = _extract_ps("Invoke-Install", ps)
    assert "if ($admission -eq 'reuse') {" in install
    assert "New-SignedVenv" not in install and "Install-SiblingPlugins" not in install
    update = _extract_ps("Invoke-Update", ps)
    assert "if ($admission -eq 'reuse') {" in update
    assert "New-SignedVenv" not in update and "Install-SiblingPlugins" not in update
    assert "Install-SiblingPlugins -Reinstall" in _extract_ps("Invoke-UpdateSlotBuild", ps)
    sh = _INSTALL_SH.read_text(encoding="utf-8")
    sh_install = _extract_sh("do_install", sh)
    assert '[[ "${SLOT_ADMISSION:-}" == reuse ]]' in sh_install
    assert "_uv_venv_resilient" not in sh_install
    assert "_install_sibling_plugins" not in sh_install
    assert "_install_sibling_plugins" not in _extract_sh("do_update", sh)
    core = _extract_sh("_update_core", sh)
    reuse = core.index('[[ "${SLOT_ADMISSION:-}" == reuse ]]')
    assert reuse < core.index("_uv_venv_resilient")
    assert reuse < core.index("_install_sibling_plugins reinstall || return 1")


# --------------------------------------------------------------------------
# Create-once: every slot mutation precedes verification and publication
# --------------------------------------------------------------------------

_SH_BUILD_STUBS = """_uv_venv_resilient() { rec BUILT; return 0; }
_uv_pip_install_resilient() { return 0; }
_resolve_ssh_manager() { echo lib; }
_resolve_credential_relay() { echo lib; }
_resolve_zdd() { echo lib; }
_resolve_single_instance_lease() { echo lib; }
_resolve_config_migrate() { echo lib; }
_runtime_healthy() { rec HEALTH; return 0; }
_install_sibling_plugins() {
    [[ -e "$VENV_DIR/.install-complete.json" ]] && rec PUBLISHED_BEFORE_PRUNE
    rec PRUNED
    return __PRUNE_RC__
}
"""

_PS_BUILD_STUBS = """function New-SignedVenv { Rec 'BUILT'; return $true }
function Invoke-UvPipInstallResilient { return [pscustomobject]@{ ExitCode = 0; Output = '' } }
function Resolve-SshManager { return 'lib' }
function Resolve-CredentialRelay { return 'lib' }
function Resolve-Zdd { return 'lib' }
function Resolve-SingleInstanceLease { return 'lib' }
function Resolve-ConfigMigrate { return 'lib' }
function Remove-ConsoleTrampolines { param([string]$VenvDir) }
function Install-SiblingPlugins {
    param([switch]$Reinstall)
    if (Test-Path -LiteralPath (Join-Path $VenvDir '.install-complete.json')) { Rec 'PUBLISHED_BEFORE_PRUNE' }
    Rec 'PRUNED'
    if (__PRUNE_FAILS__) { throw 'provider still importable after prune' }
}
"""


def _assert_mutation_precedes_publication(
    tmp_path: Path, result: subprocess.CompletedProcess, *, prune_fails: bool, update: bool
) -> None:
    events = _events(tmp_path)
    detail = f"rc={result.returncode}\nevents={events}\nstdout={result.stdout}\nstderr={result.stderr}"
    slot = tmp_path / "install" / "versions" / _VERSION
    assert "PRUNED" in events and "PUBLISHED_BEFORE_PRUNE" not in events, detail
    if prune_fails:
        # A failed prune publishes nothing and activates nothing; an update
        # also rolls back by discarding the never-published slot.
        assert result.returncode == 1, detail
        for sentinel in ("HEALTH", "ACTIVATED"):
            assert sentinel not in events, detail
        assert not _slot_marker(tmp_path).exists(), detail
        if update:
            assert not slot.exists(), detail
        return
    assert result.returncode == 50, detail
    assert events.index("BUILT") < events.index("PRUNED") < events.index("HEALTH"), detail
    assert events.index("HEALTH") < events.index("ACTIVATED"), detail
    assert _slot_marker(tmp_path).exists(), detail


@pytest.mark.timeout(_SH_TIMEOUT_SECONDS)
@pytest.mark.skipif(_bash() is None, reason="a conformant bash is not available")
@pytest.mark.parametrize("prune_fails", [False, True], ids=["prune-ok", "prune-fails"])
@pytest.mark.parametrize("action", ["do_install", "do_update"])
def test_sh_slot_is_pruned_before_verification_publication_and_activation(
    tmp_path: Path, action: str, prune_fails: bool
) -> None:
    """Provider pruning mutates the slot, so it must run inside the build --
    never after the completion marker is published or the slot activated."""
    stubs = _SH_BUILD_STUBS.replace("__PRUNE_RC__", "1" if prune_fails else "0")
    result = _run_sh(_sh_harness(tmp_path, "incomplete", stubs + action))
    _assert_mutation_precedes_publication(
        tmp_path, result, prune_fails=prune_fails, update=action == "do_update"
    )


@pytest.mark.skipif(_PWSH is None, reason="pwsh is not available")
@pytest.mark.parametrize("prune_fails", [False, True], ids=["prune-ok", "prune-fails"])
@pytest.mark.parametrize("action", ["Invoke-Install", "Invoke-Update"])
def test_ps1_slot_is_pruned_before_verification_publication_and_activation(
    tmp_path: Path, action: str, prune_fails: bool
) -> None:
    stubs = _PS_BUILD_STUBS.replace("__PRUNE_FAILS__", "$true" if prune_fails else "$false")
    # Invoke-UpdateSlotBuild checks the slot interpreter exists; the harness's
    # interpreter is a function, so give its name a file to find.
    (tmp_path / "FakeSlotPython").write_text("", encoding="utf-8")
    prefix = f"Set-Location -LiteralPath {_ps_quote(tmp_path)}\n"
    harness = _ps_harness(tmp_path, "incomplete", prefix + stubs + action)
    result = _run_ps(harness)
    _assert_mutation_precedes_publication(
        tmp_path, result, prune_fails=prune_fails, update=action == "Invoke-Update"
    )


_SH_FAKE_VENV = r"""fake_venv() {
    mkdir -p "$1/bin" "$1/lib/agent_codespaces"
    echo stale > "$1/lib/agent_codespaces/__init__.py"
    cat > "$1/bin/python" <<'EOF'
#!/bin/sh
here="$(cd "$(dirname "$0")/.." && pwd)"
case "$*" in
    *sysconfig*) echo "$here/lib" ;;
    *find_spec*) if [ -d "$here/lib/agent_codespaces" ]; then printf agent_codespaces; exit 1; fi ;;
esac
exit 0
EOF
    chmod +x "$1/bin/python"
}
uv() { rec "UV:$*"; }
"""


@pytest.mark.timeout(_SH_TIMEOUT_SECONDS)
@pytest.mark.skipif(_bash() is None, reason="a conformant bash is not available")
@pytest.mark.parametrize("legacy", ["link", "real"])
def test_sh_provider_prune_never_rewrites_a_published_slot_through_the_venv_link(
    tmp_path: Path, legacy: str
) -> None:
    """Pruning runs before activation, while a stale `venv` symlink may still
    target the previously published slot: only a real legacy venv is pruned."""
    published = '"$INSTALL_DIR/versions/0.9.0"'
    setup = (
        f"fake_venv {published}\n"
        'MSYS=winsymlinks:nativestrict ln -s ' + published
        + ' "$INSTALL_DIR/venv" || { echo NOSYMLINK; exit 0; }\n'
        '[[ -L "$INSTALL_DIR/venv" ]] || { echo NOSYMLINK; exit 0; }\n'
        if legacy == "link"
        else 'fake_venv "$INSTALL_DIR/venv"\n'
    )
    action = (
        _SH_FAKE_VENV
        + 'fake_venv "$VENV_DIR"\n'
        + setup
        + 'if _remove_vendored_providers; then echo "RC=0"; else echo "RC=1"; fi\n'
    )
    harness = _sh_harness(tmp_path, "incomplete", action, extra=("_remove_vendored_providers",))
    result = _run_sh(harness)
    detail = f"rc={result.returncode}\nevents={_events(tmp_path)}\n{result.stdout}{result.stderr}"
    if "NOSYMLINK" in result.stdout:
        pytest.skip("this bash cannot create a native symlink")
    assert result.returncode == 0 and "RC=0" in result.stdout, detail
    install = tmp_path / "install"
    assert not (install / "versions" / _VERSION / "lib" / "agent_codespaces").exists(), detail
    if legacy == "link":
        stale = install / "versions" / "0.9.0" / "lib" / "agent_codespaces" / "__init__.py"
        assert stale.exists(), "pruning rewrote a published slot through the venv link"
        assert not any("/venv/bin/python" in e for e in _events(tmp_path)), detail
    else:
        assert not (install / "venv" / "lib" / "agent_codespaces").exists(), detail


# --------------------------------------------------------------------------
# Rollback never deletes a published slot
# --------------------------------------------------------------------------


def _assert_published_slot_survives_failed_update(
    tmp_path: Path, result: subprocess.CompletedProcess
) -> None:
    events = _events(tmp_path)
    detail = f"rc={result.returncode}\nevents={events}\nstdout={result.stdout}\nstderr={result.stderr}"
    assert result.returncode == 1, detail
    assert "HEALTH" in events and "ACTIVATED" not in events, detail
    assert _slot_marker(tmp_path).exists(), "update rollback deleted an already-published slot"
    assert _tree(tmp_path / "install" / "versions" / _VERSION) == _tree(tmp_path / "before-slot"), detail


@pytest.mark.skipif(_bash() is None, reason="a conformant bash is not available")
def test_sh_failed_update_keeps_a_reused_published_slot(tmp_path: Path) -> None:
    """A matching completed slot that fails its health check is only not
    activated: it was published by an earlier run and is create-once."""
    action = "_runtime_healthy() { rec HEALTH; return 1; }\ndo_update"
    result = _run_sh(_sh_harness(tmp_path, "match-inactive", action))
    _assert_published_slot_survives_failed_update(tmp_path, result)


@pytest.mark.skipif(_PWSH is None, reason="pwsh is not available")
def test_ps1_failed_update_keeps_a_reused_published_slot(tmp_path: Path) -> None:
    action = "function Test-RuntimeHealthy { Rec 'HEALTH'; return $false }\nInvoke-Update"
    result = _run_ps(_ps_harness(tmp_path, "match-inactive", action))
    _assert_published_slot_survives_failed_update(tmp_path, result)

# --------------------------------------------------------------------------
# The install lock fails closed and never reports a blocked run as success
# --------------------------------------------------------------------------


@pytest.mark.skipif(_bash() is None, reason="a conformant bash is not available")
@pytest.mark.parametrize("action", ["do_install", "do_update"])
@pytest.mark.parametrize(("lock_rc", "expected_rc"), [(75, 75), (1, 1)], ids=["busy", "fault"])
def test_sh_blocked_or_faulted_lock_never_proceeds_or_reports_success(
    tmp_path: Path, action: str, lock_rc: int, expected_rc: int
) -> None:
    stub = f"_enter_install_lock() {{ return {lock_rc}; }}\n"
    result = _run_sh(_sh_harness(tmp_path, "incomplete", stub + action))
    events = _events(tmp_path)
    detail = f"rc={result.returncode}\nevents={events}\n{result.stdout}{result.stderr}"
    assert result.returncode == expected_rc, detail
    for sentinel in ("PROCEEDED", "DRAINED", "MARKER", "MANIFEST", "ACTIVATED"):
        assert sentinel not in events, detail
    expected = "blocked by lock" if lock_rc == 75 else "refusing to proceed unlocked"
    assert expected in result.stdout + result.stderr, detail


@pytest.mark.skipif(_bash() is None, reason="a conformant bash is not available")
def test_sh_pid_fallback_lock_reports_contention_as_75(tmp_path: Path) -> None:
    """Without flock the pid-symlink fallback still serializes: a live holder
    makes the waiter time out with 75 (contention), never proceed."""
    action = (
        "export COPILOT_EXT_NO_FLOCK=1 COPILOT_EXT_NO_PYFLOCK=1 COPILOT_PLUGIN_INSTALL_LOCK_TIMEOUT_SEC=1\n"
        'mkdir -p "$INSTALL_DIR"\n'
        'if ! MSYS=winsymlinks:nativestrict ln -s "$$" "$INSTALL_DIR/.install.lock.pid"; then\n'
        '    echo NOSYMLINK; exit 0\n'
        'fi\n'
        '[[ -L "$INSTALL_DIR/.install.lock.pid" ]] || { echo NOSYMLINK; exit 0; }\n'
        "rc=0; _enter_install_lock || rc=$?\n"
        'echo "RC=$rc"\n'
    )
    harness = _sh_harness(tmp_path, "incomplete", action, extra=("_enter_install_lock",))
    text = harness.read_text(encoding="utf-8").replace("_enter_install_lock() { return 0; }\n", "")
    harness.write_text(text, encoding="utf-8", newline="\n")
    result = _run_sh(harness)
    if "NOSYMLINK" in result.stdout:
        pytest.skip("this bash cannot create a native symlink")
    assert "RC=75" in result.stdout, result.stdout + result.stderr


@pytest.mark.skipif(_PWSH is None, reason="pwsh is not available")
@pytest.mark.parametrize("action", ["Invoke-Install", "Invoke-Update"])
def test_ps1_busy_lock_exits_75_without_proceeding(tmp_path: Path, action: str) -> None:
    stub = "function Enter-InstallLock { return $false }\n"
    result = _run_ps(_ps_harness(tmp_path, "incomplete", stub + action))
    events = _events(tmp_path)
    detail = f"rc={result.returncode}\nevents={events}\n{result.stdout}{result.stderr}"
    assert result.returncode == 75, detail
    for sentinel in ("BUILT", "DRAINED", "MARKER", "MANIFEST", "ACTIVATED"):
        assert sentinel not in events, detail
    assert "blocked by lock" in result.stdout + result.stderr, detail


@pytest.mark.skipif(_PWSH is None, reason="pwsh is not available")
def test_ps1_lock_fault_fails_closed(tmp_path: Path) -> None:
    """A lock that cannot be opened for a non-contention reason (here the lock
    path is a directory) exits 1 instead of installing unlocked."""
    (tmp_path / "install" / ".install.lock").mkdir(parents=True)
    action = "$r = Enter-InstallLock -TimeoutSec 2\nWrite-Host \"RESULT=$r\"\nexit 0"
    result = _run_ps(_ps_harness(tmp_path, "incomplete", action, extra=("Enter-InstallLock",)))
    detail = f"rc={result.returncode}\n{result.stdout}{result.stderr}"
    assert result.returncode == 1, detail
    assert "RESULT=" not in result.stdout, detail
    assert "refusing to proceed unlocked" in result.stdout + result.stderr, detail


_LOCK_HOLDER = """
import os, sys, time
path = sys.argv[1]
handle = open(path, "a+b")
if os.name != "nt":
    import fcntl
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
print("HELD", flush=True)
time.sleep(60)
"""


@pytest.mark.skipif(_PWSH is None, reason="pwsh is not available")
def test_ps1_lock_held_by_another_process_is_contention(tmp_path: Path) -> None:
    """A lock genuinely held by another process is contention on every OS:
    Windows reports a sharing violation, Linux/macOS report EWOULDBLOCK (11/35).
    The installer waits out its deadline and reports busy (exit 75 upstream),
    never a lock fault (exit 1)."""
    lock = tmp_path / "install" / ".install.lock"
    lock.parent.mkdir(parents=True)
    holder = subprocess.Popen(
        [sys.executable, "-c", _LOCK_HOLDER, str(lock)], stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "HELD"
        action = "$r = Enter-InstallLock -TimeoutSec 1\nWrite-Host \"RESULT=$r\"\nexit 0"
        result = _run_ps(_ps_harness(tmp_path, "incomplete", action, extra=("Enter-InstallLock",)))
    finally:
        holder.kill()
        holder.wait(timeout=30)
    detail = f"rc={result.returncode}\n{result.stdout}{result.stderr}"
    assert result.returncode == 0, detail
    assert "RESULT=False" in result.stdout, detail
    assert "refusing to proceed unlocked" not in result.stdout + result.stderr, detail


def _sh_real_lock_harness(tmp_path: Path, action: str) -> Path:
    harness = _sh_harness(tmp_path, "incomplete", action, extra=("_enter_install_lock",))
    text = harness.read_text(encoding="utf-8").replace("_enter_install_lock() { return 0; }\n", "")
    harness.write_text(text, encoding="utf-8", newline="\n")
    return harness


@pytest.mark.skipif(_bash() is None, reason="a conformant bash is not available")
@pytest.mark.parametrize(("flock_rc", "expected"), [(75, 75), (1, 1), (66, 1)],
                         ids=["conflict", "flock-error", "flock-oserr"])
def test_sh_only_a_flock_conflict_is_contention(tmp_path: Path, flock_rc: int, expected: int) -> None:
    """flock is asked to exit 75 on a conflict; any other failure is a fault."""
    action = (
        f'flock() {{ [[ " $* " == *" -E 75 "* ]] || return 99; return {flock_rc}; }}\n'
        "rc=0; _enter_install_lock || rc=$?\n"
        'echo "RC=$rc"\n'
    )
    result = _run_sh(_sh_real_lock_harness(tmp_path, action))
    assert f"RC={expected}" in result.stdout, result.stdout + result.stderr


@pytest.mark.skipif(_bash() is None, reason="a conformant bash is not available")
@pytest.mark.parametrize(("owner_alive", "expected"), [(True, 75), (False, 1)],
                         ids=["live-owner", "stale-owner-reap-stuck"])
def test_sh_pid_fallback_contention_needs_a_live_owner(
    tmp_path: Path, owner_alive: bool, expected: int
) -> None:
    """Only a live holder is contention (75). A stale owner whose recovery
    cannot proceed (its reap mutex is held) is a lock fault (1) at the
    deadline. Shims stand in for symlinks so this runs without that privilege."""
    action = (
        "export COPILOT_EXT_NO_FLOCK=1 COPILOT_EXT_NO_PYFLOCK=1 COPILOT_PLUGIN_INSTALL_LOCK_TIMEOUT_SEC=1\n"
        'mkdir -p "$INSTALL_DIR"\n'
        "ln() { return 1; }\nreadlink() { echo 424242; }\nrm() { :; }\n"
        f"kill() {{ return {0 if owner_alive else 1}; }}\n"
        "rc=0; _enter_install_lock || rc=$?\n"
        'echo "RC=$rc"\n'
    )
    result = _run_sh(_sh_real_lock_harness(tmp_path, action))
    assert f"RC={expected}" in result.stdout, result.stdout + result.stderr


@pytest.mark.skipif(_bash() is None, reason="a conformant bash is not available")
def test_sh_pid_fallback_rejects_a_link_created_inside_a_directory(tmp_path: Path) -> None:
    """`ln -s` into a directory at the lock path succeeds by creating a child;
    that is not this run's lock, so it is a fault, never an acquisition."""
    action = (
        "export COPILOT_EXT_NO_FLOCK=1 COPILOT_EXT_NO_PYFLOCK=1 COPILOT_PLUGIN_INSTALL_LOCK_TIMEOUT_SEC=1\n"
        'mkdir -p "$INSTALL_DIR"\n'
        "ln() { return 0; }\nreadlink() { return 1; }\n"
        "rc=0; _enter_install_lock || rc=$?\n"
        'echo "RC=$rc"\n'
    )
    result = _run_sh(_sh_real_lock_harness(tmp_path, action))
    assert "RC=1" in result.stdout, result.stdout + result.stderr


@pytest.mark.skipif(_bash() is None, reason="a conformant bash is not available")
def test_sh_pid_fallback_reap_path_directory_is_a_fault_not_the_mutex(tmp_path: Path) -> None:
    """A directory at the reap path makes `ln -s` succeed by creating a child,
    so every contender would think it holds the recovery mutex and could unlink
    a lock another contender just took. The run must fail closed without ever
    touching the lock link. Shims stand in for symlinks (no privilege needed)."""
    action = (
        "export COPILOT_EXT_NO_FLOCK=1 COPILOT_EXT_NO_PYFLOCK=1 COPILOT_PLUGIN_INSTALL_LOCK_TIMEOUT_SEC=1\n"
        'mkdir -p "$INSTALL_DIR"\n'
        'ln() { [[ "${@: -1}" == *.reap ]]; }\n'
        'readlink() { [[ "$1" == *.reap ]] && return 1; echo 424242; }\n'
        "kill() { return 1; }\n"
        'rm() { echo "RM:$*"; }\n'
        "rc=0; _enter_install_lock || rc=$?\n"
        'echo "RC=$rc"\n'
    )
    result = _run_sh(_sh_real_lock_harness(tmp_path, action))
    detail = result.stdout + result.stderr
    assert "RC=1" in result.stdout, detail
    assert not any(
        line.startswith("RM:") and line.rstrip().endswith("/.install.lock.pid")
        for line in result.stdout.splitlines()
    ), detail


_FLOCK_HOLDER = """
import fcntl, sys, time
handle = open(sys.argv[1], "a+b")
fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
print("HELD", flush=True)
time.sleep(60)
"""


@pytest.mark.skipif(_bash() is None or os.name == "nt", reason="needs bash and POSIX flock(2)")
@pytest.mark.parametrize("held", [True, False], ids=["held-by-flock", "free"])
def test_sh_without_flock_binary_shares_the_flock_lock(tmp_path: Path, held: bool) -> None:
    """Without a flock binary the installer still takes flock(2) on
    .install.lock (through Python), so it excludes a flock or PowerShell
    holder of the same file instead of using a separate protocol."""
    lock = tmp_path / "install" / ".install.lock"
    lock.parent.mkdir(parents=True)
    action = (
        "export COPILOT_EXT_NO_FLOCK=1 COPILOT_PLUGIN_INSTALL_LOCK_TIMEOUT_SEC=1\n"
        "rc=0; _enter_install_lock || rc=$?\n"
        'echo "RC=$rc"\n'
    )
    holder = None
    if held:
        holder = subprocess.Popen(
            [sys.executable, "-c", _FLOCK_HOLDER, str(lock)], stdout=subprocess.PIPE, text=True,
        )
        assert holder.stdout is not None and holder.stdout.readline().strip() == "HELD"
    try:
        result = _run_sh(_sh_real_lock_harness(tmp_path, action))
    finally:
        if holder is not None:
            holder.kill()
            holder.wait(timeout=30)
    assert f"RC={75 if held else 0}" in result.stdout, result.stdout + result.stderr
    assert not (lock.parent / ".install.lock.pid").exists()


@pytest.mark.skipif(_bash() is None, reason="a conformant bash is not available")
def test_sh_pid_fallback_unattributable_lock_path_is_a_fault(tmp_path: Path) -> None:
    """A lock path that can never be created or attributed (here a regular
    file) is a lock fault, not another install holding the lock."""
    action = (
        "export COPILOT_EXT_NO_FLOCK=1 COPILOT_EXT_NO_PYFLOCK=1 COPILOT_PLUGIN_INSTALL_LOCK_TIMEOUT_SEC=1\n"
        'mkdir -p "$INSTALL_DIR"\n'
        'echo not-a-link > "$INSTALL_DIR/.install.lock.pid"\n'
        "rc=0; _enter_install_lock || rc=$?\n"
        'echo "RC=$rc"\n'
    )
    result = _run_sh(_sh_real_lock_harness(tmp_path, action))
    assert "RC=1" in result.stdout, result.stdout + result.stderr