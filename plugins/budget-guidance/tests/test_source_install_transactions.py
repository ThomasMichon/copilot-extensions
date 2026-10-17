"""Execute source-qualified installer seams without host provisioning or network."""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN / "scripts"
VERSION = "1.2.3"
spec = importlib.util.spec_from_file_location(
    "budget_source_fixture",
    SCRIPTS / "versioned_runtime_source.py",
)
assert spec and spec.loader
source = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = source
spec.loader.exec_module(source)


def _q(value: object, shell: str) -> str:
    text = str(value)
    if shell == "bash":
        text = text.replace("\\", "/")
        if os.name == "nt" and len(text) > 1 and text[1] == ":":
            text = "/" + text[0].lower() + text[2:]
        return "'" + text.replace("'", "'\\''") + "'"
    return "'" + text.replace("'", "''") + "'"


def _function(name: str, shell: str) -> str:
    suffix = "sh" if shell == "bash" else "ps1"
    text = (SCRIPTS / f"install.{suffix}").read_text(encoding="utf-8")
    prefix = rf"^{re.escape(name)}\(\)" if shell == "bash" else rf"^function {name}"
    match = re.search(prefix + r" \{.*?^\}", text, re.M | re.S)
    assert match, name
    body = match.group()
    return body if shell == "bash" else body.replace("$PSScriptRoot", "$ScriptDir")


def _snapshot(root: Path) -> Path:
    value = (root / "payload-dir").read_text().strip()
    if os.name == "nt" and re.match(r"^/[A-Za-z]/", value):
        value = value[1].upper() + ":" + value[2:]
    return Path(value)


def _python_code(text: str) -> str:
    encoded = base64.b64encode(text.encode()).decode()
    return f"import base64;exec(base64.b64decode('{encoded}'))"


def _payload(root: Path) -> Path:
    payload = root / "payload"
    shutil.copytree(SCRIPTS, payload / "scripts")
    package = payload / "src" / "budget_guidance"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("from agent_procutil import VALUE\n")
    library = payload / "libs" / "agent-procutil"
    (library / "src" / "agent_procutil").mkdir(parents=True)
    (library / "src" / "agent_procutil" / "__init__.py").write_text("VALUE = 42\n")
    (library / "pyproject.toml").write_text('[project]\nname="agent-procutil"\nversion="1.0.0"\n')
    (payload / "pyproject.toml").write_text(
        f'[project]\nname="budget-guidance"\nversion="{VERSION}"\n'
        '[tool.uv.sources]\nagent-procutil = { path = "libs/agent-procutil", editable = false }\n',
    )
    (payload / "plugin.json").write_text(json.dumps({"name": PLUGIN.name, "version": VERSION}))
    for directory in ("tests", "node_modules"):
        (payload / directory).mkdir()
        (payload / directory / "hashed.txt").write_text("must survive the stamp\n")
    digest = source.assembly_hash(payload)
    identity = source.SourceIdentity(
        PLUGIN.name,
        VERSION,
        "a" * 40,
        "b" * 40,
        digest,
        source.runtime_version(VERSION, "a" * 40, digest),
    )
    (payload / "runtime-source.json").write_text(json.dumps(identity.document()))
    return payload


def _setup(root: Path, payload: Path, shell: str) -> str:
    def q(value: object) -> str:
        return _q(value, shell)

    if shell == "bash":
        names = (
            "_source_runtime_identity",
            "_initialize_source_runtime",
            "_source_runtime_unchanged",
            "_budget_procutil_source",
            "_payload_hash",
            "_versioned_slot_clean",
            "_versioned_mark_complete",
            "_versioned_activate",
        )
        return f"""
set -euo pipefail
PLUGIN_DIR={q(payload)}
SCRIPT_DIR="$PLUGIN_DIR/scripts"
PKG_SRC_DIR="$PLUGIN_DIR/src/budget_guidance"
INSTALL_DIR={q(root / "install")}
LOCAL_BIN={q(root / "bin")}
LINK_DIR="$INSTALL_DIR/.venv"
VENV_DIR="$LINK_DIR"
VENV_PYTHON="$VENV_DIR/bin/python"
LINK_PYTHON="$VENV_PYTHON"
PACKAGE_VERSION='{VERSION}'
SRC_VERSION="$PACKAGE_VERSION"
SOURCE_RUNTIME_IDENTITY=""
VERSIONED_RUNTIME=1
PY={q(sys.executable)}
_bootstrap_python() {{ printf '%s' "$PY"; }}
_fail() {{ printf '%s\\n' "$*" >&2; }}
_ok() {{ :; }}
deploy_binstub() {{ :; }}
{chr(10).join(_function(name, shell) for name in names)}
"""
    names = (
        "Get-SourceRuntimeIdentity",
        "Initialize-SourceRuntime",
        "Test-SourceRuntimeUnchanged",
        "Get-BudgetProcutilSource",
        "Get-PayloadHash",
        "Invoke-VersionedSlotClean",
        "Invoke-VersionedMarkComplete",
        "Invoke-VersionedActivate",
        "Invoke-Stamp",
    )
    return f"""
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$PluginDir = {q(payload)}
$ScriptDir = Join-Path $PluginDir 'scripts'
. (Join-Path $ScriptDir 'versioned_runtime_native.ps1')
$PkgSrcDir = Join-Path $PluginDir 'src/budget_guidance'
$InstallDir = {q(root / "install")}
$LocalBin = {q(root / "bin")}
$LinkDir = Join-Path $InstallDir '.venv'
$VenvDir = $LinkDir
$PackageVersion = '{VERSION}'
$SrcVersion = $PackageVersion
$SourceRuntimeIdentity = $null
$VersionedRuntime = $true
$utf8NoBom = [Text.UTF8Encoding]::new($false)
$RealPython = {q(sys.executable)}
function Get-BootstrapPython {{ return $RealPython }}
function Write-Ok {{ param($value) }}
function Write-Step {{ param($value) }}
function Write-Fail {{ param($value) throw $value }}
function Deploy-SelfProvisioningBinstub {{ }}
{chr(10).join(_function(name, shell) for name in names)}
"""


def _host(shell: str) -> str:
    if shell == "bash" and os.name == "nt":
        git = shutil.which("git")
        candidate = Path(git).parent.parent / "bin" / "bash.exe" if git else None
        host = str(candidate) if candidate and candidate.is_file() else None
    else:
        host = shutil.which(shell)
    if not host:
        pytest.skip(f"{shell} unavailable")
    return host


def _run(root: Path, shell: str, text: str) -> subprocess.CompletedProcess[str]:
    host = _host(shell)
    harness = root / ("transaction.sh" if shell == "bash" else "transaction.ps1")
    harness.write_text(text, encoding="utf-8", newline="\n")
    argv = (
        [host, str(harness)]
        if shell == "bash"
        else [
            host,
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(harness),
        ]
    )
    return subprocess.run(  # noqa: S603 - trusted native host and isolated fixture script
        argv,
        cwd=root,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


@pytest.mark.parametrize("shell", ["pwsh", "powershell", "bash"])
@pytest.mark.parametrize("boundary", ["selection", "admission", "completion", "activation"])
@pytest.mark.parametrize("mutation", ["bytes", "identity", "removed", "appeared"])
def test_native_source_refuses_changed_inputs(
    tmp_path: Path,
    shell: str,
    boundary: str,
    mutation: str,
) -> None:
    payload = _payload(tmp_path)
    root = tmp_path / "install"
    root.mkdir()
    previous = {
        "current-version": b"1.2.2",
        "payload-dir": b"previous",
        "stamped-version": b"1.2.2",
        "redirect": b"previous-runtime",
        "versions/1.2.2/.install-complete.json": b"old-completion",
    }
    for name, content in previous.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(content)
    if mutation == "appeared":
        shutil.copyfile(payload / "runtime-source.json", tmp_path / "saved-receipt.json")
        (payload / "runtime-source.json").unlink()
    mutate = {
        "bytes": "p.joinpath('tests/hashed.txt').write_text('changed')",
        "removed": "p.joinpath('runtime-source.json').unlink()",
        "identity": (
            "f=p/'runtime-source.json'; d=json.loads(f.read_text()); "
            f"d['source_tree']={('invalid' if boundary == 'selection' else 'c' * 40)!r}; "
            "f.write_text(json.dumps(d))"
        ),
        "appeared": (
            "p.joinpath('runtime-source.json').write_bytes("
            "p.parent.joinpath('saved-receipt.json').read_bytes())"
        ),
    }[mutation]
    code = f"import json,sys; from pathlib import Path; p=Path(sys.argv[1]); {mutate}"
    text = _setup(tmp_path, payload, shell)
    if shell == "bash":
        if boundary != "selection":
            text += "_initialize_source_runtime\n"
        text += f'"$PY" -I -c {_q(code, shell)} "$PLUGIN_DIR"\n'
        text += {
            "selection": "_initialize_source_runtime\n",
            "admission": "_versioned_slot_clean\n",
            "completion": "_versioned_mark_complete\n",
            "activation": "_versioned_activate\n",
        }[boundary]
    else:
        if boundary != "selection":
            text += "Initialize-SourceRuntime\n"
        text += f"& $RealPython -I -c {_q(code, shell)} $PluginDir\n"
        text += {
            "selection": "Initialize-SourceRuntime\n",
            "admission": "Invoke-VersionedSlotClean\n",
            "completion": "Invoke-VersionedMarkComplete\n",
            "activation": "Invoke-VersionedActivate\n",
        }[boundary]
    result = _run(tmp_path, shell, text)
    detail = result.stdout + result.stderr
    if boundary == "selection" and mutation in ("removed", "appeared"):
        assert result.returncode == 0, detail
    else:
        assert result.returncode != 0, detail
        assert "identity" in detail.lower(), detail
    for name, content in previous.items():
        assert (root / name).read_bytes() == content
    assert not list(root.glob(f"versions/{VERSION}*/.install-complete.json"))


def _stamp_text(root: Path, payload: Path, shell: str) -> str:
    text = _setup(root, payload, shell)
    if shell == "bash":
        installer = (SCRIPTS / "install.sh").read_text()
        start = installer.index('if [[ "$ACTION" == "stamp" ]]; then')
        end = installer.index('if [[ "$ACTION" == "status" ]]; then', start)
        return text + "_initialize_source_runtime\nACTION=stamp\n" + installer[start:end]
    return text + "Initialize-SourceRuntime\nInvoke-Stamp\n"


@pytest.mark.parametrize("shell", ["pwsh", "powershell", "bash"])
@pytest.mark.parametrize("corrupt_copy", [False, True])
def test_native_source_stamp_preserves_assembly_before_redirects(
    tmp_path: Path,
    shell: str,
    corrupt_copy: bool,
) -> None:
    payload = _payload(tmp_path)
    text = _stamp_text(tmp_path, payload, shell)
    root = tmp_path / "install"
    root.mkdir()
    previous = {
        "payload-dir": b"previous",
        "stamped-version": b"1.2.2",
        "current-version": b"1.2.2",
    }
    for name, content in previous.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(content)
    if corrupt_copy:
        if shell == "bash":
            wrapper = (
                _function("_source_runtime_identity", shell).replace(
                    "_source_runtime_identity()",
                    "_real_source_runtime_identity()",
                    1,
                )
                + """
_source_runtime_identity() {
    if [[ "${1:-$PLUGIN_DIR}" != "$PLUGIN_DIR" ]]; then
        printf corrupted >> "$1/tests/hashed.txt"
    fi
    _real_source_runtime_identity "$@"
}
"""
            )
            text = text.replace(
                "_initialize_source_runtime\nACTION=stamp",
                wrapper + "_initialize_source_runtime\nACTION=stamp",
            )
        else:
            text = text.replace(
                "Initialize-SourceRuntime\nInvoke-Stamp",
                """
function Get-BootstrapPython {
    if ((Get-Variable snapTmp -ErrorAction SilentlyContinue) -and
        (Test-Path "$snapTmp/tests/hashed.txt")) {
        Add-Content -LiteralPath "$snapTmp/tests/hashed.txt" 'corrupted'
    }
    return $RealPython
}
Initialize-SourceRuntime
Invoke-Stamp
""",
            )
    result = _run(tmp_path, shell, text)
    detail = result.stdout + result.stderr
    if corrupt_copy:
        assert result.returncode != 0, detail
        assert "identity" in detail.lower()
        for name, content in previous.items():
            assert (root / name).read_bytes() == content
        return
    assert result.returncode == 0, detail
    snapshot = _snapshot(root)
    assert source.read_source_identity(snapshot, VERSION, plugin=PLUGIN.name)
    assert (snapshot / "pyproject.toml").read_bytes() == (payload / "pyproject.toml").read_bytes()
    for directory in ("tests", "node_modules"):
        assert (snapshot / directory / "hashed.txt").read_bytes() == (
            payload / directory / "hashed.txt"
        ).read_bytes()


@pytest.mark.parametrize("shell", ["pwsh", "powershell", "bash"])
@pytest.mark.parametrize("changed_receipt", [False, True])
def test_native_source_snapshot_reuse_never_rewrites_existing_bytes(
    tmp_path: Path,
    shell: str,
    changed_receipt: bool,
) -> None:
    payload = _payload(tmp_path)
    first = _run(tmp_path, shell, _stamp_text(tmp_path, payload, shell))
    assert first.returncode == 0, first.stdout + first.stderr
    root = tmp_path / "install"
    snapshot = _snapshot(root)
    if changed_receipt:
        receipt = snapshot / "runtime-source.json"
        data = json.loads(receipt.read_text())
        data["source_tree"] = "c" * 40
        receipt.write_text(json.dumps(data))
        (root / "payload-dir").write_bytes(b"previous-payload")
    for path in (snapshot, *snapshot.rglob("*")):
        os.utime(path, ns=(1_600_000_000_000_000_000, 1_600_000_000_000_000_000))
    before = {
        path.relative_to(snapshot).as_posix(): (
            path.stat().st_mtime_ns,
            path.read_bytes() if path.is_file() else None,
        )
        for path in (snapshot, *snapshot.rglob("*"))
    }
    selected = (root / "payload-dir").read_bytes()
    repeated = _run(tmp_path, shell, _stamp_text(tmp_path, payload, shell))
    detail = repeated.stdout + repeated.stderr
    assert (repeated.returncode != 0) == changed_receipt, detail
    assert (root / "payload-dir").read_bytes() == selected
    after = {
        path.relative_to(snapshot).as_posix(): (
            path.stat().st_mtime_ns,
            path.read_bytes() if path.is_file() else None,
        )
        for path in (snapshot, *snapshot.rglob("*"))
    }
    assert after == before
    if changed_receipt:
        assert "identity" in detail.lower(), detail
    assert not list(snapshot.parent.glob(f"{snapshot.name}.tmp-*"))


@pytest.mark.parametrize("shell", ["pwsh", "powershell", "bash"])
def test_native_generation_reuses_and_outlives_source_snapshot(tmp_path: Path, shell: str) -> None:
    if shell == "bash" and os.name == "nt":
        pytest.skip("Full POSIX construction requires POSIX venv layout and FIFO authority")
    payload = _payload(tmp_path)
    stamped = _run(tmp_path, shell, _stamp_text(tmp_path, payload, shell))
    assert stamped.returncode == 0, stamped.stdout + stamped.stderr
    snapshot = _snapshot(tmp_path / "install")
    shutil.rmtree(payload)
    runtime = json.loads((snapshot / "runtime-source.json").read_text())["runtime_version"]
    build = """
import pathlib,shutil,subprocess,sys,venv
p,slot=pathlib.Path(sys.argv[1]),pathlib.Path(sys.argv[2])
venv.EnvBuilder(with_pip=False).create(slot)
python=slot/('Scripts/python.exe' if sys.platform=='win32' else 'bin/python')
site=pathlib.Path(subprocess.check_output(
    [str(python),'-I','-c','import sysconfig; print(sysconfig.get_path("purelib"))'],
    text=True).strip())
shutil.copytree(p/'src/budget_guidance',site/'budget_guidance',dirs_exist_ok=True)
shutil.copytree(p/'libs/agent-procutil/src/agent_procutil',site/'agent_procutil',dirs_exist_ok=True)
"""
    for expected in (False, True):
        text = _setup(tmp_path, snapshot, shell)
        if shell == "bash":
            text += f"""
_initialize_source_runtime
_versioned_slot_clean
[[ "$_RUNTIME_CONSTRUCTION_REUSE" == {int(expected)} ]]
if [[ "$_RUNTIME_CONSTRUCTION_REUSE" == 0 ]]; then
    "$PY" -I -c {_q(build, shell)} "$PLUGIN_DIR" "$VENV_DIR"
fi
_versioned_activate
_runtime_construction_finish
"""
        else:
            text += f"""
Initialize-SourceRuntime
try {{
    Invoke-VersionedSlotClean
    if ($script:RuntimeConstructionReuse -ne {"$true" if expected else "$false"}) {{
        throw 'Wrong admission'
    }}
    if (-not $script:RuntimeConstructionReuse) {{
        & $RealPython -I -c {_q(_python_code(build), shell)} $PluginDir $VenvDir
        if ($LASTEXITCODE -ne 0) {{ throw 'Fixture build failed' }}
    }}
    if (-not (Invoke-VersionedActivate)) {{ throw 'Activation failed' }}
}} finally {{ Stop-RuntimeConstruction }}
"""
        result = _run(tmp_path, shell, text)
        assert result.returncode == 0, result.stdout + result.stderr
    slot = tmp_path / "install" / "versions" / runtime
    assert (tmp_path / "install" / "current-version").read_text().strip() == runtime
    assert (slot / ".install-complete.json").is_file()
    shutil.rmtree(snapshot)
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    result = subprocess.run(  # noqa: S603 - interpreter belongs to the isolated fixture slot
        [str(python), "-I", "-c", "from budget_guidance import VALUE; assert VALUE == 42"],
        capture_output=True,
        text=True,
        timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
