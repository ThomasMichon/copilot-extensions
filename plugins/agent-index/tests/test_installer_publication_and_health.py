"""Publication ordering and runtime repair boundaries in disposable fixtures."""
from __future__ import annotations

import os
import json
import shlex
import shutil
import subprocess
import sys
import threading
import venv
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from test_install_engine_adoption import environment, function, run_ps


@pytest.mark.parametrize("host", ["pwsh", "powershell", "bash"])
@pytest.mark.parametrize("outcome", [
    "native-failure", "stale", "health-stale", "wrong-owner", "ready",
    "client", "unconfigured", "no-service",
    "existing-host-failure",
])
def test_native_installer_service_result_and_selected_identity(tmp_path, host, outcome):
    if host == "bash" and os.name == "nt":
        pytest.skip("This is a native POSIX consumer, not Windows Bash qualification")
    if host == "powershell" and os.name != "nt":
        pytest.skip("Windows PowerShell is unavailable")
    executable = shutil.which(host)
    if not executable:
        pytest.skip(f"{host} unavailable")

    from zdd import routing

    root = tmp_path / "runtime"
    root.mkdir()
    selected = root / "versions" / "2.0.0"
    selected.mkdir(parents=True)
    preserved = selected / "preserved-content"
    preserved.write_bytes(b"selected immutable content")
    engine = root / "engine"
    engine.mkdir()
    (engine / "current-version").write_text("independent-engine", encoding="utf-8")
    (root / "current-version").write_text("2.0.0", encoding="utf-8")
    before = {path: path.read_bytes() for path in (preserved, engine / "current-version")}
    version = "2.0.0" if outcome in {"ready", "health-stale", "wrong-owner"} else "1.0.0"

    class Health(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({
                "status": "ok", "plugin": "agent-index",
                "version": "1.0.0" if outcome == "health-stale" else version,
                "pid": os.getpid() + int(outcome == "wrong-owner"), "installationId": "",
            }).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Health)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        routing.publish_active(
            root, bind="127.0.0.1", port=server.server_port, pid=os.getpid(), version=version,
        )
        log = tmp_path / "native-calls"
        native = tmp_path / "native.py"
        code = 37 if outcome in {"native-failure", "existing-host-failure"} else 0
        native.write_text(
            "import os, pathlib, subprocess, sys\n"
            "args = sys.argv[1:]\n"
            "if '-m' in args and args[args.index('-m') + 1:] == ['agent_index', 'deploy']:\n"
            " pathlib.Path(os.environ['INDEX_FIXTURE_CALLS']).write_text('deploy')\n"
            " print('native deployment diagnostic', file=sys.stderr)\n"
            f" raise SystemExit({code})\n"
            "raise SystemExit(subprocess.call([sys.executable, *args]))\n",
            encoding="utf-8",
        )
        runtime = tmp_path / ("native.ps1" if os.name == "nt" else "native")
        runtime.write_text(
            f"& '{sys.executable}' '{native}' @args\nexit $LASTEXITCODE\n" if os.name == "nt" else
            f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(native))} \"$@\"\n",
            encoding="utf-8",
        )
        if os.name != "nt":
            runtime.chmod(0o755)
        env = environment(tmp_path)
        env.update({
            "AGENT_INDEX_HOME": str(root), "AGENT_INDEX_ROUTING_DIR": str(root),
            "INDEX_FIXTURE_CALLS": str(log),
        })
        role = outcome if outcome in {"client", "unconfigured"} else "host"
        if outcome == "existing-host-failure":
            role = "client"
        machine_role = "host" if outcome == "existing-host-failure" else role
        scripts = Path(__file__).resolve().parents[1] / "scripts"
        if host == "bash":
            # No systemctl is discoverable: the native owner must still run.
            env["PATH"] = str(tmp_path)
            text = f"""
set -euo pipefail
INSTALL_DIR={shlex.quote(str(root))}
LINK_PYTHON={shlex.quote(str(runtime))}
SRC_VERSION=2.0.0
NO_SERVICE={int(outcome == "no-service")}
_ok() {{ echo "$*"; }}; _skip() {{ echo "$*"; }}; _step() {{ :; }}
_fail() {{ echo "$*" >&2; }}
_activation_role() {{ echo {role}; }}
_machine_role() {{ echo {machine_role}; }}
{function("_service_healthy", "sh")}
{function("_service_cutover", "sh")}
_service_cutover
"""
            command = [executable]
            harness = tmp_path / "consumer.sh"
        else:
            engine_helpers = scripts.parents[2] / "libs" / "installer-engine" / "installer-engine.ps1"
            text = f"""
$ErrorActionPreference = 'Stop'
. '{engine_helpers}'
$InstallDir = '{root}'
$LinkPython = '{runtime}'
$SrcVersion = '2.0.0'
$NoService = ${str(outcome == "no-service").lower()}
function Write-Ok {{ param($m) Write-Host $m }}
function Write-Skip {{ param($m) Write-Host $m }}
function Write-Warn {{ param($m) Write-Host $m }}
function Write-Step {{ param($m) }}
function Get-ActivationRole {{ return '{role}' }}
function Get-MachineRole {{ return '{machine_role}' }}
function Write-ServiceFiles {{ }}
{function("Get-ActiveServicePort", "ps1")}
{function("Test-ServiceHealthy", "ps1")}
{function("Invoke-ServiceCutover", "ps1")}
Invoke-ServiceCutover
"""
            command = [executable, "-NoProfile", "-NonInteractive", "-File"]
            harness = tmp_path / "consumer.ps1"
        harness.write_text(text, encoding="utf-8")
        result = subprocess.run(
            [*command, str(harness)], env=env, cwd=tmp_path, capture_output=True,
            text=True, timeout=25,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        skipped = outcome in {"client", "unconfigured", "no-service"}
        success = skipped or outcome == "ready"
        assert (result.returncode == 0) is success, result.stdout + result.stderr
        assert log.exists() is not skipped
        if outcome == "ready":
            assert "2.0.0 is ready" in result.stdout
        if not success:
            assert "is ready" not in result.stdout
        assert {path: path.read_bytes() for path in before} == before
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


@pytest.mark.parametrize("failure", ["filesystem", "probe"])
def test_required_server_repair_exception_refuses_activation_and_restores_preference(tmp_path, failure):
    (tmp_path / "slot/server").mkdir(parents=True)
    injection = (
        "function Test-IndexVenv { param($Dir,$Python) throw 'probe fixture failure' }"
        if failure == "probe" else
        "function Test-IndexVenv { param($Dir,$Python) return $false }\n"
        "function Remove-Item { param($LiteralPath) throw 'filesystem fixture failure' }"
    )
    result = run_ps(tmp_path, function("New-IndexVenv", "ps1") +
                    function("Install-ServerVenv", "ps1") + f"""
$env:OS = 'Installer_Test'
$VenvDir = '{tmp_path / "slot"}'
$ErrorActionPreference = 'Stop'
function Write-Warn {{ param($Msg) [Console]::Error.WriteLine($Msg) }}
{injection}
function Resolve-Zdd {{ throw 'package install must not follow failed repair' }}
if (Install-ServerVenv -InstallRole host -PythonCmd forbidden) {{ throw 'failed host was accepted' }}
if ($ErrorActionPreference -ne 'Stop') {{ throw 'preference was not restored' }}
'host-activation-refused'
""")
    assert result.returncode == 0, result.stderr
    assert "host-activation-refused" in result.stdout
    assert f"{failure} fixture failure" in result.stderr
    assert "falls back to the shared venv" not in result.stderr


def publication_fixture(tmp_path: Path) -> tuple[str, Path]:
    root = tmp_path / "runtime"
    (root / "versions/2.0.0").mkdir(parents=True)
    scripts = tmp_path
    (scripts / "versioned_runtime.py").write_text(
        "import pathlib, sys\n"
        "a = sys.argv\n"
        "if 'activate' in a:\n"
        "    root = pathlib.Path(a[a.index('--root') + 1])\n"
        "    (root / 'current-version').write_text(a[a.index('activate') + 1])\n",
        encoding="utf-8",
    )
    definitions = "\n".join(function(name, "ps1") for name in (
        "Get-VerTuple", "Test-VersionLt", "New-IndexMutex", "Enter-IndexStampLock", "Enter-IndexBuildLock", "Get-IndexUv",
        "Test-IndexPublicationFresh", "Publish-FileAtomically",
        "Invoke-VersionedActivate", "Publish-IndexRuntime",
    ))
    script = definitions + f"""
$ErrorActionPreference = 'Stop'
$InstallDir = '{root}'
$PSScriptRoot = '{scripts}'
$SrcVersion = '2.0.0'
$VersionedRuntime = $true
$Force = $false
$LegacyVenvDir = '{tmp_path / "absent-legacy"}'
$VenvDir = '{root / "versions/2.0.0"}'
$VenvPython = '{sys.executable}'
function Write-Ok {{ param($Msg) }}
function Write-Skip {{ param($Msg) }}
function Write-Fail {{ param($Msg) throw $Msg }}
function Test-RuntimeOrigin {{ param($Python, $Slot) return $true }}
function Invoke-Stop {{ throw 'forbidden lifecycle mutation' }}
function Deploy-SetupGatedBinstub {{
    param($PayloadRoot)
    [IO.File]::WriteAllText((Join-Path $InstallDir 'payload-dir'), $SrcVersion)
    [IO.File]::WriteAllText((Join-Path $InstallDir 'launcher-version'), $SrcVersion)
}}
function Write-Manifest {{
    [IO.File]::WriteAllText((Join-Path $InstallDir 'manifest-version'), $SrcVersion)
}}
"""
    return script, root


def test_new_activation_cannot_enter_between_stamp_check_and_publication(tmp_path):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")
    definitions, root = publication_fixture(tmp_path)
    (root / "current-version").write_text("0.9.0", encoding="utf-8")
    old_snapshot = root / "snapshots/1.0.0-identity"
    old_snapshot.mkdir(parents=True)
    (root / "stamp-candidate-1.0.0").write_text(str(old_snapshot), encoding="utf-8")
    checked = tmp_path / "checked"
    outcome = tmp_path / "lock-outcome"
    worker = tmp_path / "worker.ps1"
    worker.write_text(definitions + f"""
$InstallDir = $InstallDir + [IO.Path]::DirectorySeparatorChar
$deadline = [DateTime]::UtcNow.AddSeconds(10)
while (-not (Test-Path '{checked}')) {{
    if ([DateTime]::UtcNow -gt $deadline) {{ throw 'stamp never reached freshness check' }}
    Start-Sleep -Milliseconds 20
}}
try {{
    $probe = Enter-IndexStampLock -Scope Publish -TimeoutSeconds 0
    [void]$probe.ReleaseMutex(); $probe.Dispose()
    [IO.File]::WriteAllText('{outcome}', 'unserialized')
}} catch {{
    if ($_.Exception.Message -notlike '*Timed out waiting*') {{ throw }}
    [IO.File]::WriteAllText('{outcome}', 'blocked')
}}
if (-not (Publish-IndexRuntime)) {{ throw 'new activation failed' }}
""", encoding="utf-8")
    with subprocess.Popen(
        [pwsh, "-NoProfile", "-File", str(worker)], env=environment(tmp_path),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ) as child:
        result = run_ps(tmp_path, definitions + function("Invoke-Stamp", "ps1") + f"""
$SrcVersion = '1.0.0'
$LocalBin = '{tmp_path / "bin"}'
$probePayload = 'old-origin'
function New-IndexSnapshot {{ return '{old_snapshot}' }}
$compare = ${{function:Test-VersionLt}}
function Test-VersionLt {{
    param($A, $B)
    if (-not $script:Signalled -and $A -eq '1.0.0' -and $B -eq '0.9.0') {{
        $script:Signalled = $true
        [IO.File]::WriteAllText('{checked}', 'checked')
        $deadline = [DateTime]::UtcNow.AddSeconds(10)
        while (-not (Test-Path '{outcome}')) {{
            if ([DateTime]::UtcNow -gt $deadline) {{ throw 'activation did not attempt publication' }}
            Start-Sleep -Milliseconds 20
        }}
        if ([IO.File]::ReadAllText('{outcome}') -ne 'blocked') {{
            throw 'activation entered between the stamp check and publication'
        }}
    }}
    return (& $compare -A $A -B $B)
}}
Invoke-Stamp
""")
        stdout, stderr = child.communicate(timeout=25)
    assert result.returncode == 0, result.stderr
    assert child.returncode == 0, stdout + stderr
    for marker in ("current-version", "payload-dir", "launcher-version", "manifest-version"):
        assert (root / marker).read_text() == "2.0.0"


@pytest.mark.parametrize("authority", ["current-version", "stamped-version"])
def test_runtime_publication_rechecks_freshness_before_all_writes(tmp_path, authority):
    definitions, root = publication_fixture(tmp_path)
    (root / authority).write_text("3.0.0", encoding="utf-8")
    for name in ("payload-dir", "launcher-version", "manifest-version"):
        (root / name).write_text("newer", encoding="utf-8")
    result = run_ps(tmp_path, definitions + """
if (Publish-IndexRuntime) { throw 'older runtime was published' }
if (-not $script:RuntimePublicationSuperseded) { throw 'supersession not reported' }
""")
    assert result.returncode == 0, result.stderr
    assert (root / authority).read_text() == "3.0.0"
    for name in ("payload-dir", "launcher-version", "manifest-version"):
        assert (root / name).read_text() == "newer"

def test_direct_launcher_writer_rejects_superseded_source(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "current-version").write_text("3.0.0")
    (root / "payload-dir").write_text("newer-payload")
    result = run_ps(tmp_path, function("Deploy-SetupGatedBinstub", "ps1") + f"""
$InstallDir = '{root}'
$SrcVersion = '2.0.0'
$LocalBin = '{tmp_path / "bin"}'
Deploy-SetupGatedBinstub -PayloadRoot older-payload
""")
    assert result.returncode == 0, result.stderr
    assert (root / "payload-dir").read_text() == "newer-payload"
    assert not (tmp_path / "bin").exists()


@pytest.mark.parametrize("mode", ["healthy", "corrupt", "base", "wrong-prefix", "probe-failure"])
def test_posix_actual_interpreter_health_and_repair(tmp_path, mode):
    bash = shutil.which("bash")
    if os.name == "nt" or not bash:
        pytest.skip("native POSIX bash is unavailable")
    directory = tmp_path / "venv"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(directory)
    python = directory / "bin/python"
    if mode != "healthy":
        python.unlink()
        if mode == "base":
            python.write_text(f"#!/bin/sh\nexec '{sys._base_executable}' \"$@\"\n")
            python.chmod(0o755)
        elif mode == "wrong-prefix":
            other = tmp_path / "other"
            venv.EnvBuilder(with_pip=False, symlinks=True).create(other)
            python.write_text(f"#!/bin/sh\nexec '{other / 'bin/python'}' \"$@\"\n")
            python.chmod(0o755)
        else:
            python.write_text("not an interpreter\n" if mode == "corrupt" else "#!/bin/sh\nexit 13\n")
            python.chmod(0o755)
    log = tmp_path / "uv-calls"
    uv = tmp_path / "uv-fixture"
    uv.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$*" >> "{log}"\n'
        '[[ "$1" == venv && "$3" == --allow-existing && "$#" == 3 ]] || exit 97\n'
        f"exec {shlex.quote(sys._base_executable)} -I -c "
        + shlex.quote(
            "import pathlib, sys, venv; root = pathlib.Path(sys.argv[1]); "
            "python = root / 'bin/python'; python.unlink(missing_ok=True); "
            "venv.EnvBuilder(with_pip=False, symlinks=True).create(root)"
        ) + ' "$2"\n',
        encoding="utf-8",
    )
    uv.chmod(0o755)
    script = f"""
set -euo pipefail
{function("_test_index_venv", "sh")}
{function("_new_index_venv", "sh")}
. '{Path(__file__).resolve().parents[3] / "libs/installer-engine/installer-engine.sh"}'
_warn() {{ echo "$*" >&2; }}
UV_COMMAND='{uv}'
VENV_DIR='{directory}'
if _test_index_venv '{directory}' '{python}'; then initial=healthy; else initial=invalid; fi
echo "$initial"
_new_index_venv '{directory}' '{python}' forbidden-base 1
_test_index_venv '{directory}' '{python}'
"""
    result = subprocess.run([bash, "-c", script], env=environment(tmp_path),
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ("healthy" if mode == "healthy" else "invalid")
    assert log.exists() == (mode != "healthy")
    if log.exists():
        assert len(log.read_text().splitlines()) == 1


def test_posix_bad_created_venv_fails_explicitly(tmp_path):
    bash = shutil.which("bash")
    if os.name == "nt" or not bash:
        pytest.skip("native POSIX bash is unavailable")
    directory = tmp_path / "venv"
    script = f"""
VENV_DIR='{directory}'
{function("_test_index_venv", "sh")}
{function("_new_index_venv", "sh")}
_warn() {{ echo "$*" >&2; }}
invoke_uv_venv_resilient() {{
    mkdir -p "$2/bin"; echo fixture > "$2/pyvenv.cfg"
    printf '#!/bin/sh\\nexec "%s" "$@"\\n' '{sys._base_executable}' > "$2/bin/python"
    chmod +x "$2/bin/python"
    return 0
}}
fallback() {{ echo forbidden-fallback >&2; return 37; }}
_new_index_venv '{directory}' '{directory / "bin/python"}' fallback 1
"""
    result = subprocess.run([bash, "-c", script], env=environment(tmp_path),
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 1
    assert "uv venv failed health validation" in result.stderr
    assert "Python venv fallback failed" in result.stderr
