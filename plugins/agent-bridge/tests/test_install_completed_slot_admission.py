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

import os
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


def _fake_plugin(tmp_path: Path) -> Path:
    plugin = tmp_path / "plugin"
    (plugin / "src" / "pkg").mkdir(parents=True)
    (plugin / "pyproject.toml").write_text(
        f'[project]\nname = "pkg"\nversion = "{_VERSION}"\n', encoding="utf-8"
    )
    (plugin / "src" / "pkg" / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    return plugin


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


def _assert_outcome(tmp_path: Path, result: subprocess.CompletedProcess, expected: str) -> None:
    events = _events(tmp_path)
    detail = (
        f"rc={result.returncode}\nevents={events}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "START" in events, detail
    if expected == _PROCEED:
        assert result.returncode == 42, detail
        assert "PROCEEDED" in events, detail
    elif expected == _NOOP:
        assert result.returncode == 0, detail
        assert "PROCEEDED" not in events and "DRAINED" not in events, detail
        assert "MANIFEST" in events, detail
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
    ("Get-PayloadHash", False),
    ("Test-SlotContentCurrent", False),
    ("Get-SlotAdmission", True),
    ("Write-SlotAdmissionRefusal", True),
    ("Test-SlotIsLiveDifferentContent", True),
    ("Invoke-VersionedMarkComplete", False),
    ("Invoke-InstallSlotBuild", True),
    ("Invoke-UpdateSlotBuild", True),
    ("Invoke-Install", False),
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


def _ps_harness(tmp_path: Path, scenario: str, action: str, extra: tuple[str, ...] = ()) -> Path:
    text = _INSTALL_PS1.read_text(encoding="utf-8")
    functions = "\n\n".join(
        _extract_ps(name, text, optional=optional)
        for name, optional in (*_PS_FUNCTIONS, *((n, False) for n in extra))
    )
    body = f"""Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ScriptDir = {_ps_quote(_SCRIPTS)}
$PluginDir = {_ps_quote(_fake_plugin(tmp_path))}
$InstallDir = {_ps_quote(tmp_path / "install")}
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
    if ($args -contains 'agent_bridge') {{ Rec 'CONFIG'; $global:LASTEXITCODE = 0; return }}
    & $RealPython @args
}}
function Get-RunningProcess {{ return $null }}
function Write-DeployManifest {{ Rec 'MANIFEST' }}
function Write-UpdateMarker {{ Rec 'MARKER' }}
function Invoke-UpdateDrainStop {{ Rec 'DRAINED'; exit 43 }}
function Test-ActiveIsForward {{ return $false }}
function Get-ActiveSignature {{ return '' }}
function Clear-UpdateMarker {{ }}
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
{action}
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
    result = _run_ps(_ps_harness(tmp_path, scenario, action))
    _assert_outcome(tmp_path, result, expected)


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
        "$r = Invoke-VersionedMarkComplete\nWrite-Host \"RESULT=[$r]\"\n",
    )
    result = _run_ps(harness)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"RESULT=[{expected}]" in result.stdout, result.stdout + result.stderr
    assert _slot_marker(tmp_path).exists()
    if scenario == "conflict-inactive":
        assert _slot_marker(tmp_path).read_bytes() == (tmp_path / "before.json").read_bytes()


def test_ps1_call_sites_fail_on_refused_completion_and_guard_precedes_mutation() -> None:
    text = _INSTALL_PS1.read_text(encoding="utf-8")
    assert text.count("if (-not (Invoke-VersionedMarkComplete)) { throw") == 2
    for name, mutations in (
        ("Invoke-Update", ("Write-UpdateMarker\n", "Invoke-UpdateDrainStop -", "Invoke-UpdateSlotBuild -")),
        ("Invoke-Install", ("Invoke-InstallSlotBuild\n",)),
    ):
        body = _extract_ps(name, text)
        refusal = body.index("exit 1", body.index("$admission = Get-SlotAdmission"))
        assert refusal < min(body.index(m) for m in mutations), name


# --------------------------------------------------------------------------
# POSIX (bash)
# --------------------------------------------------------------------------

_SH_FUNCTIONS = (
    ("_payload_hash", False),
    ("_bootstrap_python", False),
    ("_versioned_current", False),
    ("_versioned_mark_complete", False),
    ("_slot_admission", True),
    ("_slot_admission_refusal", True),
    ("_guard_published_slot", True),
    ("_install_slot_build", True),
    ("_update_core", False),
    ("do_install", False),
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


def _sh_harness(tmp_path: Path, scenario: str, action: str, extra: tuple[str, ...] = ()) -> Path:
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
        'case "$*" in *agent_bridge*) '
        f"echo CONFIG >> {_sh_quote(_posix(tmp_path / 'events.txt'))}; exit 0 ;; esac\n"
        f"exec {_sh_quote(_posix(sys.executable))} \"$@\"\n"
    )
    for name in ("python3", "python"):
        path = shim_dir / name
        path.write_text(shim, encoding="utf-8", newline="\n")
        path.chmod(0o755)
    script = f"""set -euo pipefail
SCRIPT_DIR={_sh_quote(_posix(_SCRIPTS))}
PLUGIN_DIR={_sh_quote(_posix(_fake_plugin(tmp_path)))}
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
_write_update_marker() {{ rec MARKER; }}
_clear_update_marker() {{ :; }}
_update_lifecycle_drain_stop() {{ rec DRAINED; exit 43; }}
_active_is_forward() {{ return 1; }}
_active_signature() {{ :; }}
_get_pid() {{ return 1; }}
SYSTEMD_UNIT=agent-bridge-admission-test.service
_install_sibling_plugins() {{ rec PRUNED; }}
_runtime_healthy() {{ rec HEALTH; return 0; }}
_versioned_activate() {{ rec ACTIVATED; exit 50; }}
_versioned_slot_clean() {{ rec PROCEEDED; exit 42; }}
_venv_healthy() {{ return 1; }}
_uv_venv_resilient() {{ rec BUILT; exit 42; }}
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
        timeout=120,
        check=False,
    )


@pytest.mark.skipif(_bash() is None, reason="a conformant bash is not available")
@pytest.mark.parametrize("action", ["do_update", "do_install"])
@pytest.mark.parametrize(("scenario", "expected"), _ADMISSION_CASES)
def test_sh_completed_slot_admission(
    tmp_path: Path, action: str, scenario: str, expected: str
) -> None:
    result = _run_sh(_sh_harness(tmp_path, scenario, action))
    _assert_outcome(tmp_path, result, expected)


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
        prefix + 'if _versioned_mark_complete; then echo "MC=ok"; else echo "MC=fail"; fi\n',
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
    assert (
        '_versioned_mark_complete || { _fail "Versioned completion publication failed"; exit 1; }'
        in text
    )
    assert "|| true" not in _extract_sh("_versioned_mark_complete", text)
    update = _extract_sh("do_update", text)
    guard = update.index("if _guard_published_slot; then")
    assert guard < update.index("_write_update_marker\n")
    assert guard < update.index('_update_lifecycle_drain_stop "')
    install = _extract_sh("do_install", text)
    assert install.index("if _guard_published_slot; then") < install.index("_install_slot_build\n")


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
        "$r = New-SignedVenv\nWrite-Host \"RESULT=[$r]\"\n",
        extra=("Invoke-VersionedSlotClean", "New-SignedVenv"),
    )
    result = _run_ps(harness)
    detail = result.stdout + result.stderr
    assert result.returncode == 0, detail
    assert ("BUILT" in _events(tmp_path)) is built, detail
    assert f"RESULT=[{built}]" in result.stdout, detail
    assert (tmp_path / "install" / "versions" / _VERSION / "partial.txt").exists() is not built


@pytest.mark.skipif(_bash() is None, reason="a conformant bash is not available")
@pytest.mark.parametrize(
    ("scenario", "built"), [("incomplete", True), ("incomplete-active", False)]
)
def test_sh_refused_slot_preparation_never_builds_over_the_slot(
    tmp_path: Path, scenario: str, built: bool
) -> None:
    harness = _sh_harness(tmp_path, scenario, "do_install", extra=("_versioned_slot_clean",))
    result = _run_sh(harness)
    detail = f"rc={result.returncode}\n{result.stdout}{result.stderr}"
    assert ("BUILT" in _events(tmp_path)) is built, detail
    assert result.returncode == (42 if built else 1), detail
    assert (tmp_path / "install" / "versions" / _VERSION / "partial.txt").exists() is not built


def test_slot_preparation_refusal_is_propagated_by_every_caller() -> None:
    ps = _INSTALL_PS1.read_text(encoding="utf-8")
    assert "if (-not (Invoke-VersionedSlotClean)) { return $false }" in _extract_ps(
        "New-SignedVenv", ps
    )
    sh = _INSTALL_SH.read_text(encoding="utf-8")
    assert "|| true" not in _extract_sh("_versioned_slot_clean", sh)
    assert "    _versioned_slot_clean || exit 1\n" in _extract_sh("_install_slot_build", sh)
    assert "_versioned_slot_clean || return 1\n" in _extract_sh("_update_core", sh)

def test_reused_slot_skips_every_slot_mutation_in_both_installers() -> None:
    """A matching completed slot is activated as-is (#5472): the build,
    package install and provider pruning are all behind the reuse branch."""
    ps = _INSTALL_PS1.read_text(encoding="utf-8")
    install = _extract_ps("Invoke-Install", ps)
    assert "if ($admission -eq 'reuse') {" in install
    assert "New-SignedVenv" not in install and "Install-SiblingPlugins" not in install
    update = _extract_ps("Invoke-Update", ps)
    assert "if ($admission -eq 'reuse') {" in update
    assert "New-SignedVenv" not in update
    assert "if ($admission -ne 'reuse') {\n        Install-SiblingPlugins -Reinstall" in update
    sh = _INSTALL_SH.read_text(encoding="utf-8")
    sh_install = _extract_sh("do_install", sh)
    assert '[[ "${SLOT_ADMISSION:-}" == reuse ]]' in sh_install
    assert "_uv_venv_resilient" not in sh_install
    core = _extract_sh("_update_core", sh)
    assert core.index('[[ "${SLOT_ADMISSION:-}" == reuse ]]') < core.index("_uv_venv_resilient")
    assert '[[ "${SLOT_ADMISSION:-}" != reuse ]]; then\n        _install_sibling_plugins reinstall' in sh
