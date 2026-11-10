"""Actual UV/native construction with local artifact and credential fixtures."""

from __future__ import annotations

import importlib.metadata
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import threading

import agent_procutil
from packaging.requirements import Requirement
import pytest

from ..test_install_snapshot_ps1 import (
    _isolated_install_env, _no_service_provision_command, _stage_payload,
)

BACKEND = r'''
import pathlib,re,tomllib,zipfile

def get_requires_for_build_wheel(config_settings=None):
    return []

def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    root=pathlib.Path(__file__).parent
    project=tomllib.loads((root/"pyproject.toml").read_text())["project"]
    name=project["name"].replace("-","_")
    version=re.sub(r"-dev(\d+)",r".dev\1",project["version"])
    wheel=f"{name}-{version}-py3-none-any.whl"
    info=f"{name}-{version}.dist-info"
    metadata=f"Metadata-Version: 2.1\nName: {project['name']}\nVersion: {version}\n"
    metadata+="".join(f"Requires-Dist: {item}\n" for item in project.get("dependencies",[]))
    for extra,requirements in project.get("optional-dependencies",{}).items():
        metadata+=f"Provides-Extra: {extra}\n"
        for item in requirements:
            requirement,sep,marker=item.partition(";")
            condition=f"({marker}) and " if sep else ""
            metadata+=f"Requires-Dist: {requirement}; {condition}extra == '{extra}'\n"
    entries=project.get("entry-points",{})
    with zipfile.ZipFile(pathlib.Path(wheel_directory)/wheel,"w") as archive:
        for member in (root/"src").rglob("*"):
            if member.is_file():
                archive.write(member,member.relative_to(root/"src").as_posix())
        archive.writestr(info+"/METADATA",metadata+"\n")
        archive.writestr(info+"/WHEEL","Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr(info+"/RECORD","")
        if entries:
            archive.writestr(info+"/entry_points.txt","".join(
                "["+group+"]\n"+"".join(key+" = "+value+"\n" for key,value in values.items())
                for group,values in entries.items()
            ))
    return wheel
'''

CONSUMER = r'''
import contextlib,io,json,os,pathlib,sys
from agent_vault import cli,ssh_key
import agent_vault
root=pathlib.Path(sys.argv[1])
assert pathlib.Path(agent_vault.__file__).is_relative_to(pathlib.Path(sys.prefix))
database=root/"fixture.kdbx"
database.write_bytes(b"private credential-owner fixture")
os.environ["KPDB"]=str(database)
record=ssh_key._generate_record()
metadata=ssh_key._read_record(record)
def send(request,**kwargs):
    if request["action"]=="ping":
        return {"ok":True}
    assert request["kpdb"]==str(database)
    assert kwargs["strict_endpoint"] is True
    if request["action"]=="ssh-key-prepare":
        return {"ok":True,"status":"reused",**metadata}
    assert request["action"]=="ssh-key-read"
    return {"ok":True,"value":record}
cli.send_command=send
cli.ensure_service=lambda: (_ for _ in ()).throw(AssertionError("unexpected activation"))
output=io.StringIO()
with contextlib.redirect_stdout(output):
    assert cli.main(["ssh-key","prepare","--entry","SSH/node-a","--require-running","--json"])==0
prepared=json.loads(output.getvalue())
assert prepared["fingerprint"]==metadata["fingerprint"]
output=io.StringIO()
directory=root/"keys"
with contextlib.redirect_stdout(output):
    assert cli.main(["ssh-key","install","--entry","SSH/node-a","--directory",str(directory),
        "--expected-fingerprint",prepared["fingerprint"],"--require-running","--json"])==0
installed=json.loads(output.getvalue())
assert installed["status"]=="installed"
assert "BEGIN OPENSSH" not in output.getvalue() and record not in output.getvalue()
assert len(list(directory.iterdir()))==2
print(json.dumps({"prepare":True,"protected_install":True,"installed_runtime":True}))
'''


def _run(argv, env, cwd):
    result = subprocess.run(
        [str(value) for value in argv], env=env, cwd=cwd,
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=120, **agent_procutil.no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def _backend(package):
    manifest = package / "pyproject.toml"
    body = manifest.read_text(encoding="utf-8")
    body = re.sub(
        r"(?ms)^\[build-system\]\n.*?(?=^\[|\Z)",
        '[build-system]\nrequires=[]\nbuild-backend="offline_backend"\nbackend-path=["."]\n\n',
        body,
    )
    manifest.write_text(body, encoding="utf-8")
    (package / "offline_backend.py").write_text(BACKEND, encoding="utf-8")


def _external_wheel(tmp_path, name, module, pool, uv, env):
    package = tmp_path / ("artifact-" + name)
    source = package / "src"
    source.mkdir(parents=True)
    distribution = importlib.metadata.distribution(name)
    shutil.copytree(
        distribution.locate_file(module), source / module,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    if name == "cffi":
        for member in distribution.files:
            if member.name.startswith("_cffi_backend") and member.suffix in {".pyd", ".so"}:
                shutil.copyfile(distribution.locate_file(member), source / member.name)
    requirements = [
        value for value in distribution.requires or []
        if not Requirement(value).marker or Requirement(value).marker.evaluate({"extra": ""})
    ]
    (package / "pyproject.toml").write_text(
        f'[project]\nname="{name}"\nversion="{distribution.version}"\n'
        f"dependencies={json.dumps(requirements)}\n"
        '[build-system]\nrequires=[]\nbuild-backend="offline_backend"\nbackend-path=["."]\n',
        encoding="utf-8",
    )
    _backend(package)
    _run([uv, "build", "--offline", "--wheel", "--out-dir", pool, package], env, tmp_path)


@contextmanager
def _artifact_index(pool):
    wheels = {path.name: path.read_bytes() for path in pool.glob("*.whl")}

    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if self.path.startswith("/simple/"):
                name = self.path.removeprefix("/simple/").strip("/")
                files = [{
                    "filename": filename, "url": "/artifacts/" + filename,
                    "hashes": {"sha256": hashlib.sha256(body).hexdigest()},
                    "upload-time": "2020-01-01T00:00:00Z",
                } for filename, body in wheels.items() if filename.startswith(name + "-")]
                body = json.dumps({
                    "meta": {"api-version": "1.0"}, "name": name, "files": files,
                }).encode()
                content_type = "application/vnd.pypi.simple.v1+json"
            else:
                body = wheels.get(self.path.removeprefix("/artifacts/"))
                content_type = "application/octet-stream"
            if body is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .05})
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/simple"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(5)
        assert not worker.is_alive(), "private artifact-index thread did not retire"


@pytest.fixture
def native_inputs(tmp_path_factory):
    yield from native_profile_inputs(tmp_path_factory)


def native_profile_inputs(tmp_path_factory, *, crypto=True):
    tmp_path = tmp_path_factory.mktemp("vp")
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    uv = shutil.which("uv")
    assert pwsh and uv, "the selected native packet requires PowerShell and the runner's uv"
    payload = _stage_payload(tmp_path)
    home = tmp_path / "home"
    env = _isolated_install_env(home)
    env.update({
        "UV_NO_INDEX": "1", "UV_NO_CACHE": "1",
        "UV_DEFAULT_INDEX": "https://fixture.invalid/simple",
        "PATH": str(Path(sys._base_executable).parent) + os.pathsep + env["PATH"],
    })
    pool = tmp_path / "artifacts"
    pool.mkdir()
    _external_wheel(tmp_path, "psutil", "psutil", pool, uv, env)
    if crypto:
        _external_wheel(tmp_path, "cryptography", "cryptography", pool, uv, env)
        _external_wheel(tmp_path, "cffi", "cffi", pool, uv, env)
        _external_wheel(tmp_path, "pycparser", "pycparser", pool, uv, env)
    for package in [payload, *(payload / "libs").iterdir()]:
        if (package / "pyproject.toml").is_file():
            _backend(package)
    env.pop("UV_NO_INDEX")
    with _artifact_index(pool) as index:
        env["UV_DEFAULT_INDEX"] = index
        yield payload, home, env, pwsh


@pytest.mark.timeout(180)
def test_native_profile_construction_reuse_update_and_installed_ssh_consumer(native_inputs):
    _exercise_native_profile(*native_inputs)


@pytest.mark.timeout(120)
@pytest.mark.parametrize("profile", ["ssh", "legacy-base"])
def test_source_profile_stamp_survives_publisher_and_validates_retained_helpers(
    native_inputs, monkeypatch, profile,
):
    payload, home, env, pwsh = native_inputs
    plugin = Path(__file__).resolve().parents[2]
    repository = plugin.parents[1]
    monkeypatch.syspath_prepend(str(repository / "tools"))
    release = runpy.run_path(str(repository / "tools" / "materialize_main.py"))
    result = release["materialize_installer_engine_ref_into"](
        source_consumer_dir=plugin, dest_consumer_dir=payload,
        canonical_root=repository,
    )
    assert not any(message.startswith("SKIP ") for message in result), result
    monkeypatch.syspath_prepend(str(payload / "scripts"))
    import versioned_runtime_source as source

    version = json.loads((payload / "plugin.json").read_text())["version"]
    digest = source.assembly_hash(payload)
    identity = source.SourceIdentity(
        "agent-vault", version, "a" * 40, "b" * 40, digest,
        source.runtime_version(version, "a" * 40, digest),
    )
    (payload / "runtime-source.json").write_text(json.dumps(identity.document()))
    if profile == "ssh":
        env["AGENT_VAULT_INSTALL_PROFILE"] = "ssh"
    else:
        env.pop("AGENT_VAULT_INSTALL_PROFILE", None)
    installer = payload / "scripts" / "install.ps1"
    _run(_no_service_provision_command(home, installer, pwsh, action="stamp"), env, home)
    root = home / ".agent-vault"
    snapshot = Path((root / "payload-dir").read_text().strip())
    assert (root / "stamped-version").read_text().strip() == identity.runtime_version
    assert not (root / "current-version").exists()
    _run(_no_service_provision_command(home, installer, pwsh), env, home)
    selected = (
        source.runtime_version(version + "+ssh", identity.source_commit, digest)
        if profile == "ssh" else identity.runtime_version
    )
    slot = root / "versions" / selected
    completed = {str(p.relative_to(slot)): (p.read_bytes(), p.stat().st_mtime_ns)
                 for p in slot.rglob("*") if p.is_file()}
    shutil.rmtree(payload)
    shutil.rmtree(payload.parents[1] / "libs")
    env.pop("AGENT_VAULT_INSTALL_PROFILE", None)
    installer = snapshot / "scripts" / "install.ps1"
    _run(_no_service_provision_command(home, installer, pwsh), env, home)
    assert {str(p.relative_to(slot)): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in slot.rglob("*") if p.is_file()} == completed
    assert (root / "current-version").read_text().strip() == selected
    manifest = json.loads((root / "deploy-manifest.json").read_text())
    assert manifest["source"]["version"] == version and manifest["runtime_version"] == selected
    assert manifest["runtime_profile"] == ("ssh" if profile == "ssh" else "base")
    assert manifest["runtime_source"] == identity.document()
    receipt = json.loads((root / "versions" / selected / "runtime-profile.json").read_text())
    assert receipt["runtimeSource"] == identity.document()
    assert (root / "stamped-version").read_text().strip() == identity.runtime_version
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if profile == "ssh":
        result = _run([python, "-I", "-B", "-c", CONSUMER, home], env, home)
        assert json.loads(result.stdout)["installed_runtime"] is True
    else:
        _run([python, "-I", "-B", "-c", "import agent_vault"], env, home)
    helper = slot / ".runtime-bootstrap" / "runtime_launcher.py"
    helper.write_text("fixture retained-helper corruption\n")
    before = (root / "current-version").read_bytes()
    refused = subprocess.run(
        _no_service_provision_command(home, installer, pwsh),
        env=env, cwd=home, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=60, **agent_procutil.no_window_kwargs(),
    )
    assert refused.returncode != 0
    assert (root / "current-version").read_bytes() == before
    assert helper.read_text() == "fixture retained-helper corruption\n"


def _exercise_native_profile(payload, home, env, pwsh):
    root = home / ".agent-vault"
    installer = payload / "scripts" / "install.ps1"
    sentinel = payload / "tests" / "assembly-sentinel.py"
    sentinel.parent.mkdir(exist_ok=True)
    sentinel.write_text("STAMP_INPUT_SENTINEL = True\n")
    owner = home / ".copilot" / "installed-plugins" / "example" / "agent-vault"
    owner.parent.mkdir(parents=True)
    shutil.copytree(payload, owner)
    settings = home / ".github" / "copilot" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({
        "extraKnownMarketplaces": {
            "example": {"source": {"source": "github", "repo": "Example/Plugins"}},
        },
    }))
    env.update({
        "COPILOT_PLUGIN_ROOT": str(owner), "COPILOT_PLUGIN_STAGED_FROM": str(owner),
        "COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT": str(home),
    })
    env["AGENT_VAULT_INSTALL_PROFILE"] = "base"
    _run(_no_service_provision_command(home, installer, pwsh, action="stamp"), env, home)
    snapshot = Path((root / "payload-dir").read_text().strip())
    assert not (snapshot / "tests").exists()
    receipt = snapshot / "snapshot-provenance.json"
    assert json.loads(receipt.read_text())["schema"] == "copilot-extensions.legacy-snapshot-source"
    provenance = (receipt.read_bytes(), receipt.stat().st_mtime_ns)
    installed = _run(
        _no_service_provision_command(home, snapshot / "scripts" / "install.ps1", pwsh), env, home,
    )
    assert "PATH: Added " in installed.stdout
    base = (root / "current-version").read_text().strip()
    base_slot = root / "versions" / base
    before = {str(p.relative_to(base_slot)): (p.read_bytes(), p.stat().st_mtime_ns)
              for p in base_slot.rglob("*") if p.is_file()}
    _run(_no_service_provision_command(home, installer, pwsh, action="update"), env, home)
    assert (root / "current-version").read_text().strip() == base
    assert {str(p.relative_to(base_slot)): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in base_slot.rglob("*") if p.is_file()} == before
    assert not list(root.glob(".profile-inputs-*"))
    assert (receipt.read_bytes(), receipt.stat().st_mtime_ns) == provenance
    python_member = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    _run([base_slot / python_member, "-I", "-c",
          "import importlib.util; assert importlib.util.find_spec('cryptography') is None"], env, home)
    env["AGENT_VAULT_INSTALL_PROFILE"] = "ssh"
    _run(_no_service_provision_command(home, installer, pwsh), env, home)
    selected = (root / "current-version").read_text().strip()
    assert selected != base and selected.endswith("+ssh")
    assert {str(p.relative_to(base_slot)): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in base_slot.rglob("*") if p.is_file()} == before
    slot = root / "versions" / selected
    evidence = slot / ".runtime-input-origins.json"
    assert json.loads(evidence.read_text())["extras"] == ["ssh"]
    completed = {str(p.relative_to(slot)): (p.read_bytes(), p.stat().st_mtime_ns)
                 for p in slot.rglob("*") if p.is_file()}
    env.pop("AGENT_VAULT_INSTALL_PROFILE")
    _run(_no_service_provision_command(home, installer, pwsh), env, home)
    assert (root / "current-version").read_text().strip() == selected
    assert {str(p.relative_to(slot)): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in slot.rglob("*") if p.is_file()} == completed
    manifest = json.loads((root / "deploy-manifest.json").read_text())
    assert manifest["runtime_profile"] == "ssh" and manifest["runtime_version"] == selected
    project = payload / "pyproject.toml"
    version = manifest["source"]["version"]
    next_version = "9.0.0"
    project.write_text(project.read_text().replace(f'version = "{version}"', f'version = "{next_version}"'))
    _run(_no_service_provision_command(home, installer, pwsh, action="update"), env, home)
    assert (root / "current-version").read_text().strip() == next_version + "+ssh"
    assert slot.is_dir() and not base_slot.exists()
    updated_python = root / "versions" / (next_version + "+ssh") / python_member
    result = _run([updated_python, "-I", "-B", "-c", CONSUMER, home], env, home)
    assert json.loads(result.stdout) == {
        "prepare": True, "protected_install": True, "installed_runtime": True,
    }
    incumbent = (root / "current-version").read_bytes()
    project.write_text(
        project.read_text().replace(f'version = "{next_version}"', 'version = "10.0.0"')
        .replace('ssh = ["cryptography>=50.0.0"]', 'ssh = ["missing-profile-artifact==1.0"]'),
    )
    failed = subprocess.run(
        _no_service_provision_command(home, installer, pwsh, action="update"),
        env=env, cwd=home, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=120, **agent_procutil.no_window_kwargs(),
    )
    assert failed.returncode != 0 and "missing-profile-artifact" in failed.stdout + failed.stderr
    assert (root / "current-version").read_bytes() == incumbent
    _run([
        updated_python, "-I", "-B", "-c",
        "import runpy,sys; from pathlib import Path; runtime=runpy.run_path(sys.argv[1]); "
        "assert not runtime['is_complete'](Path(sys.argv[2]), '10.0.0+ssh')",
        payload / "scripts" / "versioned_runtime.py", root,
    ], env, home)
    assert json.loads((root / "runtime-profile.json").read_text())["profile"] == "ssh"
