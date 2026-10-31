"""Logger's two governed transactions and native cold/warm source refresh."""

from __future__ import annotations

import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

import pytest

from .test_install_binstub import _isolated_install_env

PLUGIN = Path(__file__).resolve().parents[1]
ENGINE = PLUGIN.parents[1] / "libs" / "installer-engine"
LIBRARIES = {
    "config-migrate": "agent-config-migrate",
    "agent-procutil": "agent-procutil",
    "single-instance-lease": "agent-single-instance-lease",
    "update-driver": "agent-update-driver",
    "dropin-registry": "agent-dropin-registry",
    "plugin-resolve": "agent-plugin-resolve",
    "plugin-activation": "agent-plugin-activation",
}
pytestmark = pytest.mark.portfolio_tier("T3")


def _function(ext: str) -> str:
    source = (PLUGIN / "scripts" / f"install.{ext}").read_text(encoding="utf-8")
    pattern = (
        r"(?ms)^install_package\(\) \{.*?^\}"
        if ext == "sh" else r"(?ms)^function Install-Package \{.*?^\}"
    )
    match = re.search(pattern, source)
    assert match
    return match.group() + "\n"


def _harness(tmp_path, ext, payload, python, *, failure="", reuse=False, native=False, index=None):
    uv = shutil.which("uv") if native else "fixture-uv"
    if native and not uv:
        pytest.skip("native UV unavailable")
    log = tmp_path / "calls.jsonl"
    activated = tmp_path / "activated"
    engine = payload / "scripts"
    if not engine.is_dir():
        engine = payload.parents[1] / "libs" / "installer-engine"
    if ext == "ps1":
        script = f"""
$ErrorActionPreference = 'Stop'
. '{engine / "installer-engine.ps1"}'
$PluginDir='{payload}'
$InstallDir='{tmp_path / "runtime"}'
$LocalBin='{tmp_path / "bin"}'
$VenvDir='{python.parent.parent}'
$VenvPython='{python}'
$script:RuntimeConstructionReuse=${str(reuse).lower()}
$publishGlobalBinstubs=$false
function Ensure-UvIndex {{}}
function Ensure-Uv {{ param($InstallRoot) '{uv}' }}
function Assert-SourceRuntime {{}}
function Invoke-VersionedSlotClean {{}}
function New-SignedVenv {{ param($VenvDir,$VenvPython,$PythonVersion,$UvCommand,$RequireSignedBase,$AllowExisting,[switch]$WithoutPip) $true }}
function Remove-ConsoleTrampolines {{ param($VenvDir) }}
function Remove-LoggerTrampolines {{ param($VenvDir) }}
function Invoke-VersionedActivate {{ Set-Content '{activated}' yes; $true }}
function Publish-PayloadSnapshot {{}}
function Write-SyncTaskLauncher {{}}
function Write-LoggerDeployManifest {{}}
function Write-Fail {{ param($Message) [Console]::Error.WriteLine($Message) }}
function Write-Ok {{ param($Message) }}
Set-Item Function:Invoke-NativeInstall -Value ${{function:Invoke-UvPipInstallResilient}}
function Invoke-UvPipInstallResilient {{
    param($UvCommand,$Arguments,$PayloadDirToScrub)
    Add-Content '{log}' (ConvertTo-Json -InputObject @($Arguments) -Compress)
    if ('{failure}' -eq 'bootstrap' -or ('{failure}' -eq 'closure' -and $Arguments -contains '--no-editable')) {{
        return @{{ExitCode=23;Output='permanent failure'}}
    }}
    if (${str(native).lower()}) {{
        return Invoke-NativeInstall -UvCommand $UvCommand -Arguments $Arguments -PayloadDirToScrub $PayloadDirToScrub
    }}
    return @{{ExitCode=0;Output=''}}
}}
""" + _function(ext) + "\nInstall-Package\n"
    else:
        if os.name == "nt":
            pytest.skip("POSIX native harness")
        script = f"""
set -euo pipefail
. '{engine / "installer-engine.sh"}'
PLUGIN_DIR='{payload}'
INSTALL_DIR='{tmp_path / "runtime"}'
LOCAL_BIN='{tmp_path / "bin"}'
VENV='{python.parent.parent}'
PUBLISH_GLOBAL_BINSTUBS=0
_RUNTIME_CONSTRUCTION_REUSE={int(reuse)}
_assert_source_runtime() {{ :; }}
_ensure_uv_index() {{ :; }}
ensure_uv() {{ echo '{uv}'; }}
_versioned_slot_clean() {{ :; }}
new_signed_venv() {{ :; }}
_versioned_activate() {{ echo yes > '{activated}'; }}
publish_payload_snapshot() {{ :; }}
ok() {{ :; }}
warn() {{ :; }}
chg() {{ :; }}
_fail() {{ echo "$*" >&2; }}
eval "$(declare -f invoke_uv_pip_install_resilient | sed '1s/invoke_uv_pip_install_resilient/invoke_native_install/')"
invoke_uv_pip_install_resilient() {{
    '{sys.executable}' -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "${{@:2}}" >> '{log}'
    if [[ '{failure}' == bootstrap || ( '{failure}' == closure && " $* " == *' --no-editable '* ) ]]; then
        echo 'permanent failure'; return 23
    fi
    if [[ '{int(native)}' == 1 ]]; then invoke_native_install "$@"; fi
}}
""" + _function(ext) + "\ninstall_package\n"
    script_path = tmp_path / f"harness.{ext}"
    script_path.write_text(script, encoding="utf-8")
    if ext == "ps1":
        shell = shutil.which("pwsh")
        if not shell:
            pytest.skip("pwsh unavailable")
        command = [shell, "-NoProfile", "-File", str(script_path)]
    else:
        command = ["bash", str(script_path)]
    env = _isolated_install_env(tmp_path / "home")
    for key in list(env):
        if key.upper().startswith(("UV_", "PIP_")):
            env.pop(key)
    env.update(UV_PYTHON_DOWNLOADS="never", UV_CACHE_DIR=str(tmp_path / "cache"))
    if index:
        env["UV_INDEX_URL"] = index
    else:
        env.update(UV_OFFLINE="1", UV_NO_INDEX="1")
    result = subprocess.run(
        command, env=env, cwd=tmp_path, capture_output=True, text=True,
        timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return result, calls, activated


def _sources(tmp_path, canonical):
    payload = tmp_path / "publisher" / "plugins" / "agent-logger"
    library_root = payload.parents[1] / "libs" if canonical else payload / "libs"
    sources = {package: library_root / directory for directory, package in LIBRARIES.items()}
    sources["agent-logger"] = payload
    for name, path in sources.items():
        path.mkdir(parents=True, exist_ok=True)
        dependencies = list(LIBRARIES.values()) if name == "agent-logger" else []
        text = (
            '[build-system]\nrequires=[]\nbuild-backend="fixture_backend"\nbackend-path=["."]\n'
            f'[project]\nname="{name}"\nversion="1.0"\ndependencies={json.dumps(dependencies)}\n'
        )
        if path == payload:
            text += "[tool.uv.sources]\n" + "".join(
                f'{package}={{path={json.dumps(os.path.relpath(root, path).replace(os.sep, "/"))},'
                f'editable={str(canonical).lower()}}}\n'
                for package, root in sources.items() if package != name
            )
        (path / "pyproject.toml").write_text(text, encoding="utf-8")
        (path / "fixture_backend.py").write_text("VALUE = 1\n", encoding="utf-8")
    (payload / "plugin.json").write_text(
        json.dumps({"name": "agent-logger", "version": "1.0",
                    "repository": "https://github.com/ThomasMichon/copilot-extensions"}),
        encoding="utf-8",
    )
    if canonical:
        roster = payload.parents[1] / ".github" / "plugin" / "marketplace.json"
        roster.parent.mkdir(parents=True)
        roster.write_text(json.dumps({"name": "copilot-extensions", "plugins": [
            {"name": "agent-logger", "version": "1.0", "source": "plugins/agent-logger"},
        ]}), encoding="utf-8")
    engine = library_root / "installer-engine" if canonical else payload / "scripts"
    engine.mkdir()
    for name in (
        "installer-engine.ps1", "installer-engine.sh",
        "installer_dependency_policy.py", "installer_dependency_config.py",
    ):
        shutil.copyfile(ENGINE / name, engine / name)
    return payload, sources


def _assert_batches(calls, sources):
    assert len(calls) == 2
    assert calls[0][-3:] == ["setuptools>=83.0.0", "pyyaml>=6.0", "--quiet"]
    batch = calls[1]
    assert "--no-editable" in batch and "--editable" not in batch
    assert set(sources) == {
        batch[i + 1] for i, argument in enumerate(batch) if argument == "--reinstall-package"
    }
    assert [Path(argument).resolve() for argument in batch if Path(argument).is_dir()] == [
        path.resolve() for path in sources.values()
    ]


@pytest.mark.parametrize("ext", ["ps1", "sh"])
@pytest.mark.parametrize("canonical", [False, True])
@pytest.mark.parametrize("failure", ["", "bootstrap", "closure", "reuse"])
@pytest.mark.timeout(150)
def test_logger_two_batches_fail_stop_and_reuse(tmp_path, ext, canonical, failure):
    payload, sources = _sources(tmp_path, canonical)
    result, calls, activated = _harness(
        tmp_path, ext, payload, Path(sys.executable), failure=failure, reuse=failure == "reuse",
    )
    assert bool(result.returncode) == (failure in ("bootstrap", "closure")), result.stdout + result.stderr
    if failure == "reuse":
        assert not calls and not activated.exists()
    elif failure == "bootstrap":
        assert len(calls) == 1 and not activated.exists()
    else:
        _assert_batches(calls, sources)
        assert activated.exists() == (not failure)


def _wheel(directory, name, version, module):
    normalized = name.replace("-", "_")
    with zipfile.ZipFile(directory / f"{normalized}-{version}-py3-none-any.whl", "w") as wheel:
        prefix = f"{normalized}-{version}.dist-info/"
        wheel.writestr(prefix + "METADATA", f"Metadata-Version: 2.3\nName: {name}\nVersion: {version}\n")
        wheel.writestr(prefix + "WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        wheel.writestr(prefix + "RECORD", "")
        wheel.writestr(module + "/__init__.py", "VALUE = 1\n")


@pytest.fixture
def prerequisite_index(tmp_path):
    directory = tmp_path / "wheelhouse"
    directory.mkdir()
    _wheel(directory, "setuptools", "83.0.0", "setuptools")
    _wheel(directory, "pyyaml", "6.0", "yaml")

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)

        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path.startswith("/simple/"):
                name = self.path.rstrip("/").rsplit("/", 1)[-1]
                files = [{
                    "filename": wheel.name, "url": "/" + wheel.name,
                    "hashes": {"sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()},
                    "upload-time": "2020-01-01T00:00:00Z",
                } for wheel in directory.glob(name + "-*.whl")]
                body = json.dumps({"meta": {"api-version": "1.0"}, "name": name, "files": files}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.pypi.simple.v1+json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                super().do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/simple"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.mark.timeout(300)
@pytest.mark.parametrize("canonical", [False, True])
def test_logger_native_batch_installs_cold_and_refreshes_warm(tmp_path, canonical, prerequisite_index):
    payload, sources = _sources(tmp_path, canonical)
    backend = '''
import pathlib, tomllib, zipfile
VALUE = 1
def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    import yaml
    from importlib.metadata import version
    assert yaml.VALUE == 1 and version("setuptools") == "83.0.0"
    project = tomllib.loads(pathlib.Path("pyproject.toml").read_text())["project"]
    name = project["name"].replace("-", "_")
    filename = name + "-1.0-py3-none-any.whl"
    prefix = name + "-1.0.dist-info/"
    metadata = "Metadata-Version: 2.3\\nName: " + project["name"] + "\\nVersion: 1.0\\n"
    metadata += "".join("Requires-Dist: " + item + "\\n" for item in project["dependencies"])
    with zipfile.ZipFile(pathlib.Path(wheel_directory) / filename, "w") as wheel:
        wheel.writestr(prefix + "METADATA", metadata + "\\n")
        wheel.writestr(prefix + "WHEEL", "Wheel-Version: 1.0\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n")
        wheel.writestr(prefix + "RECORD", "")
        wheel.writestr(name + "/__init__.py", "VALUE = " + str(VALUE) + "\\n")
    return filename
'''
    for source in sources.values():
        (source / "fixture_backend.py").write_text(backend, encoding="utf-8")
        project = source / "pyproject.toml"
        project.write_text(
            project.read_text().replace('requires=[]', 'requires=["setuptools>=83.0.0","pyyaml>=6.0"]'),
            encoding="utf-8",
        )
    slot = tmp_path / "venv"
    subprocess.run(
        [sys._base_executable, "-I", "-m", "venv", "--copies", "--without-pip", str(slot)],
        env=_isolated_install_env(tmp_path / "home"), capture_output=True, check=True,
        timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    ext = "ps1" if os.name == "nt" else "sh"
    env = _isolated_install_env(tmp_path / "home")
    for expected in (1, 2):
        if expected == 2:
            first = next(iter(sources.values()))
            (first / "fixture_backend.py").write_text(
                backend.replace("VALUE = 1", "VALUE = 2"), encoding="utf-8",
            )
            (tmp_path / "calls.jsonl").unlink()
        result, calls, activated = _harness(
            tmp_path, ext, payload, python, native=True, index=prerequisite_index,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        _assert_batches(calls, sources)
        assert activated.exists()
        probe = subprocess.run(
            [str(python), "-I", "-c",
             "from importlib.metadata import distributions; import agent_config_migrate; "
             f"assert agent_config_migrate.VALUE == {expected}; "
             f"assert {{d.metadata['Name']: d.version for d in distributions()}} == "
             f"{dict(dict.fromkeys(sources, '1.0'), setuptools='83.0.0', pyyaml='6.0')!r}"],
            env=env, capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert probe.returncode == 0, probe.stdout + probe.stderr
