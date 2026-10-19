"""Native installer seams with real source receipts and construction ownership."""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job

PLUGIN = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN / "scripts"
VERSION = "1.2.3"
spec = importlib.util.spec_from_file_location(
    "pull_requests_source_fixture",
    SCRIPTS / "versioned_runtime_source.py",
)
assert spec and spec.loader
source = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = source
spec.loader.exec_module(source)


def _quote(value: object, shell: str) -> str:
    text = str(value)
    if shell == "bash":
        text = text.replace("\\", "/")
        if os.name == "nt" and len(text) > 1 and text[1] == ":":
            text = "/" + text[0].lower() + text[2:]
        return "'" + text.replace("'", "'\\''") + "'"
    return "'" + text.replace("'", "''") + "'"


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


def _function(name: str, shell: str) -> str:
    suffix = "sh" if shell == "bash" else "ps1"
    text = (SCRIPTS / f"install.{suffix}").read_text(encoding="utf-8")
    prefix = rf"^{re.escape(name)}\(\)" if shell == "bash" else rf"^function {name}"
    match = re.search(prefix + r" \{.*?^\}", text, flags=re.M | re.S)
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
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    # These synthetic legacy-source transactions use the real shared runtime
    # family, not the unrelated cell bootstrap/validator payload.
    for helper in SCRIPTS.glob("versioned_runtime*"):
        if helper.is_file():
            shutil.copyfile(helper, scripts / helper.name)
    package = payload / "src" / PLUGIN.name.replace("-", "_")
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("from vendored_fixture import VALUE\n")
    lib = payload / "libs" / "agent-procutil"
    (lib / "src" / "vendored_fixture").mkdir(parents=True)
    (lib / "src" / "vendored_fixture" / "__init__.py").write_text("VALUE = 42\n")
    (lib / "pyproject.toml").write_text('[project]\nname="fixture"\nversion="1.0.0"\n')
    (payload / "pyproject.toml").write_text(
        f'[project]\nname="{PLUGIN.name}"\nversion="{VERSION}"\n'
        '[tool.uv.sources]\nfixture = { path = "libs/agent-procutil", editable = false }\n',
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


def test_native_source_fixture_retains_complete_shared_runtime_family(tmp_path):
    payload = _payload(tmp_path)
    canonical = PLUGIN.parents[1] / "libs" / "versioned-runtime"
    expected = {
        helper.name: helper.read_bytes()
        for helper in canonical.glob("versioned_runtime*") if helper.is_file()
    }
    actual = {
        helper.name: helper.read_bytes() for helper in (payload / "scripts").iterdir()
    }
    assert actual == expected
    assert not (payload / "scripts" / "installation-context").exists()


def _setup(root: Path, payload: Path, shell: str) -> str:
    def q(value: object) -> str:
        return _quote(value, shell)

    if shell == "bash":
        names = (
            "_source_runtime_identity",
            "_initialize_source_runtime",
            "_source_runtime_unchanged",
            "_payload_hash",
            "_versioned_slot_clean",
            "_versioned_mark_complete",
            "_versioned_activate",
        )
        return f"""
set -euo pipefail
PLUGIN_DIR={q(payload)}
SCRIPT_DIR="$PLUGIN_DIR/scripts"
PKG_SRC_DIR="$PLUGIN_DIR/src/{PLUGIN.name.replace("-", "_")}"
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
_install_hook_files() {{ :; }}
write_simple_binstub() {{ :; }}
deploy_binstub() {{ :; }}
{chr(10).join(_function(name, shell) for name in names)}
"""
    names = (
        "Get-SourceRuntimeIdentity",
        "Initialize-SourceRuntime",
        "Test-SourceRuntimeUnchanged",
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
$PkgSrcDir = Join-Path $PluginDir 'src/{PLUGIN.name.replace("-", "_")}'
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
function Write-SimpleBinstub {{ }}
function Install-HookFiles {{ }}
function Deploy-SelfProvisioningBinstub {{ }}
{chr(10).join(_function(name, shell) for name in names)}
"""


def _run(
    root: Path, shell: str, text: str, *, timeout: float = 90,
) -> subprocess.CompletedProcess[str]:
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
    process, job = spawn_sync_in_kill_on_close_job(
        argv,
        require_job=os.name == "nt",
        allow_breakaway=False,
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **no_window_kwargs(),
        **({"start_new_session": True} if os.name != "nt" else {}),
    )
    try:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            if job is not None:
                job.terminate_and_wait(5)
            else:
                os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate(timeout=5)
            raise subprocess.TimeoutExpired(argv, timeout, stdout, stderr) from error
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    finally:
        if job is not None:
            job.close()
        elif process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()


@pytest.mark.timeout(20)
def test_native_harness_timeout_reaps_pipe_inheriting_descendant(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "_host", lambda _shell: sys.executable)
    text = """
import subprocess, sys
subprocess.Popen(
    [sys.executable, "-I", "-c", "import time; time.sleep(60)"],
    stdin=subprocess.DEVNULL, stdout=sys.stdout, stderr=sys.stderr,
    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
)
print("fixture parent exited while its child retains the pipes", flush=True)
"""
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as refused:
        _run(tmp_path, "bash", text, timeout=1)
    assert time.monotonic() - started < 10
    assert "child retains the pipes" in refused.value.output


@pytest.mark.parametrize("shell", ["pwsh", "powershell", "bash"])
@pytest.mark.parametrize("boundary", ["selection", "admission", "completion", "activation"])
@pytest.mark.parametrize("mutation", ["bytes", "removed", "identity", "appeared"])
def test_native_source_refuses_changed_inputs_before_publication(
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

    def q(value: object) -> str:
        return _quote(value, shell)

    if shell == "bash":
        select = "_initialize_source_runtime\n"
        mutation_call = f'"$PY" -I -c {q(code)} "$PLUGIN_DIR"\n'
        target = {
            "selection": "_initialize_source_runtime",
            "admission": "_versioned_slot_clean",
            "completion": "_versioned_mark_complete",
            "activation": "_versioned_activate",
        }[boundary]
        text = (
            _setup(tmp_path, payload, shell)
            + ("" if boundary == "selection" else select)
            + mutation_call
            + target
            + "\n"
        )
    else:
        target = {
            "selection": "Initialize-SourceRuntime",
            "admission": "Invoke-VersionedSlotClean",
            "completion": "Invoke-VersionedMarkComplete",
            "activation": "Invoke-VersionedActivate",
        }[boundary]
        text = (
            _setup(tmp_path, payload, shell)
            + ("" if boundary == "selection" else "Initialize-SourceRuntime\n")
            + f"& $RealPython -I -c {q(code)} $PluginDir\n{target}\n"
        )
    result = _run(tmp_path, shell, text)
    detail = result.stdout + result.stderr
    # A receipt absent from the outset is the ordinary package-version path.
    if boundary == "selection" and mutation in ("removed", "appeared"):
        assert result.returncode == 0, detail
    else:
        assert result.returncode != 0, detail
        assert "identity" in detail.lower(), detail
    for name, content in previous.items():
        assert (root / name).read_bytes() == content
    assert not list(root.glob(f"versions/{VERSION}*/.install-complete.json"))


def _stamp_text(tmp_path: Path, payload: Path, shell: str) -> str:
    text = _setup(tmp_path, payload, shell)
    if shell == "bash":
        installer = (SCRIPTS / "install.sh").read_text()
        start = installer.index('if [[ "$ACTION" == "stamp" ]]; then')
        end = installer.index('if [[ "$ACTION" == "status" ]]; then', start)
        text += "_initialize_source_runtime\nACTION=stamp\n" + installer[start:end]
    else:
        text += "Initialize-SourceRuntime\nInvoke-Stamp\n"
    return text


@pytest.mark.parametrize("shell", ["pwsh", "powershell", "bash"])
@pytest.mark.parametrize("corrupt_copy", [False, True])
def test_native_source_stamp_keeps_complete_assembly(
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
    if corrupt_copy:
        assert result.returncode != 0, result.stdout + result.stderr
        assert "identity" in (result.stdout + result.stderr).lower()
        for name, content in previous.items():
            assert (root / name).read_bytes() == content
        return
    assert result.returncode == 0, result.stdout + result.stderr
    snapshot = _snapshot(tmp_path / "install")
    assert source.read_source_identity(snapshot, VERSION, plugin=PLUGIN.name)
    for directory in ("tests", "node_modules"):
        assert (snapshot / directory / "hashed.txt").read_bytes() == (
            payload / directory / "hashed.txt"
        ).read_bytes()
    assert (snapshot / "pyproject.toml").read_bytes() == (payload / "pyproject.toml").read_bytes()


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


# This item performs stamping, fresh construction and reuse through separate
# native hosts; Windows PowerShell measured 27 seconds before any load margin.
@pytest.mark.parametrize("shell", [
    "pwsh",
    pytest.param(
        "powershell",
        marks=pytest.mark.timeout(45) if os.name == "nt" else (),
    ),
    "bash",
])
def test_native_qualified_generation_reuses_and_retains_installed_closure(
    tmp_path: Path,
    shell: str,
) -> None:
    if shell == "bash" and os.name == "nt":
        pytest.skip("Full POSIX construction requires POSIX venv layout and FIFO authority")
    payload = _payload(tmp_path)
    stamped = _run(tmp_path, shell, _stamp_text(tmp_path, payload, shell))
    assert stamped.returncode == 0, stamped.stdout + stamped.stderr
    snapshot = _snapshot(tmp_path / "install")
    shutil.rmtree(payload)
    payload = snapshot
    build = """
import pathlib,shutil,subprocess,sys,venv
p,slot,name=pathlib.Path(sys.argv[1]),pathlib.Path(sys.argv[2]),sys.argv[3]
venv.EnvBuilder(with_pip=False).create(slot)
python=slot/('Scripts/python.exe' if sys.platform=='win32' else 'bin/python')
site=pathlib.Path(subprocess.check_output(
    [str(python),'-I','-c','import sysconfig; print(sysconfig.get_path("purelib"))'],
    text=True).strip())
shutil.copytree(p/'src'/name,site/name,dirs_exist_ok=True)
shutil.copytree(p/'libs/agent-procutil/src/vendored_fixture',site/'vendored_fixture',dirs_exist_ok=True)
"""
    runtime = json.loads((payload / "runtime-source.json").read_text())["runtime_version"]

    def q(value: object) -> str:
        return _quote(value, shell)

    for expected in (False, True):
        text = _setup(tmp_path, payload, shell)
        if shell == "bash":
            text += f"""
_initialize_source_runtime
_versioned_slot_clean
[[ "$_RUNTIME_CONSTRUCTION_REUSE" == {int(expected)} ]]
if [[ "$_RUNTIME_CONSTRUCTION_REUSE" == 0 ]]; then
    "$PY" -I -c {q(build)} "$PLUGIN_DIR" "$VENV_DIR" {q(PLUGIN.name.replace("-", "_"))}
fi
_versioned_activate
_runtime_construction_finish
"""
        else:
            text += f"""
Initialize-SourceRuntime
try {{
    [void](Invoke-VersionedSlotClean)
    if ($script:RuntimeConstructionReuse -ne {"$true" if expected else "$false"}) {{
        throw 'Wrong admission'
    }}
    if (-not $script:RuntimeConstructionReuse) {{
        & $RealPython -I -c {q(_python_code(build))} `
            $PluginDir $VenvDir {q(PLUGIN.name.replace("-", "_"))}
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
    shutil.rmtree(payload)
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    result = subprocess.run(  # noqa: S603 - interpreter belongs to the isolated fixture slot
        [
            str(python),
            "-I",
            "-c",
            f"from {PLUGIN.name.replace('-', '_')} import VALUE; assert VALUE == 42",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
