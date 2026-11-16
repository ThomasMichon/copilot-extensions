"""Actual UV/native construction with local artifact and credential fixtures."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from email.parser import Parser
import importlib
import importlib.metadata
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Any
import zipfile

import agent_procutil
from packaging.requirements import Requirement
import pytest

assert __package__ is not None
_snapshot = importlib.import_module("..test_install_snapshot_ps1", __package__)
_isolated_install_env: Callable[[Path], dict[str, str]] = _snapshot._isolated_install_env
_no_service_provision_command: Callable[..., list[str]] = _snapshot._no_service_provision_command
_stage_payload: Callable[[Path], Path] = _snapshot._stage_payload
NativeInputs = tuple[Path, Path, dict[str, str], str]
ProfileState = tuple[Path, Path, Path, Path, str, str]


@dataclass
class ProfileSequence:
    inputs: NativeInputs
    selected: ProfileState | None = None
    complete: bool = False


@dataclass
class SourceProfileState:
    root: Path
    snapshot: Path
    slot: Path
    selected: str
    version: str
    source_version: str
    identity: dict[str, Any]


@dataclass
class SourceProfileSequence:
    inputs: NativeInputs
    profile: str
    selected: SourceProfileState | None = None
    complete: bool = False

BACKEND = r'''
import base64,csv,hashlib,io,pathlib,re,tomllib,zipfile

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
                archive.writestr(member.relative_to(root/"src").as_posix(),member.read_bytes())
        archive.writestr(info+"/METADATA",metadata+"\n")
        archive.writestr(info+"/WHEEL","Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        if entries:
            archive.writestr(info+"/entry_points.txt","".join(
                "["+group+"]\n"+"".join(key+" = "+value+"\n" for key,value in values.items())
                for group,values in entries.items()
            ))
        rows=[]
        for member in archive.namelist():
            body=archive.read(member)
            digest=base64.urlsafe_b64encode(hashlib.sha256(body).digest()).rstrip(b"=").decode()
            rows.append([member,"sha256="+digest,str(len(body))])
        rows.append([info+"/RECORD","",""])
        record=io.StringIO(newline="")
        csv.writer(record,lineterminator="\n").writerows(rows)
        archive.writestr(info+"/RECORD",record.getvalue())
    return wheel
'''

CONSUMER = r'''
import contextlib,hashlib,io,json,os,pathlib,sys
from agent_vault import cache,cli,kek,ssh_key
import agent_vault
root=pathlib.Path(sys.argv[1])
assert pathlib.Path(agent_vault.__file__).is_relative_to(pathlib.Path(sys.prefix))
database=root/"fixture.kdbx"
database.write_bytes(b"private credential-owner fixture")
os.environ["KPDB"]=str(database)
saved=root/"fixture-record"
first=not saved.exists()
record=ssh_key._generate_record() if first else saved.read_text()
if first:
    saved.write_text(record)
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
assert installed["status"]==("installed" if first else "reused")
assert "BEGIN OPENSSH" not in output.getvalue() and record not in output.getvalue()
assert len(list(directory.iterdir()))==2
os.environ["AGENT_VAULT_CACHE_DIR"]=str(root/"protected-cache")
os.environ["AGENT_VAULT_KEK_DIR"]=str(root/"protected-keks")
data=root/"fixture-data.json"
if first:
    assert cache.PersistentCache().put("SSH/node-a","password","fixture-value")
    sealed=kek.seal("fixture",b"fixture-sealed")
    data.write_text(json.dumps({"sealed":sealed,
        "kek_sha256":hashlib.sha256((root/"protected-keks"/"fixture.kek").read_bytes()).hexdigest()}))
preserved=json.loads(data.read_text())
assert cache.PersistentCache().get("SSH/node-a",readonly=True)=="fixture-value"
assert kek.unseal("fixture",preserved["sealed"])==b"fixture-sealed"
assert hashlib.sha256((root/"protected-keks"/"fixture.kek").read_bytes()).hexdigest()==preserved["kek_sha256"]
print(json.dumps({"prepare":True,"protected_install":True,"installed_runtime":True}))
'''


def _run(
    argv: Sequence[str | Path], env: dict[str, str], cwd: Path, *, capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [str(value) for value in argv], env=env, cwd=cwd,
        stdin=subprocess.DEVNULL, capture_output=capture, text=True,
        timeout=120, **agent_procutil.no_window_kwargs(),
    )
    assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")
    return result


def _backend(package: Path) -> None:
    manifest = package / "pyproject.toml"
    body = manifest.read_text(encoding="utf-8")
    body = re.sub(
        r"(?ms)^\[build-system\]\n.*?(?=^\[|\Z)",
        '[build-system]\nrequires=[]\nbuild-backend="offline_backend"\nbackend-path=["."]\n\n',
        body,
    )
    manifest.write_text(body, encoding="utf-8")
    (package / "offline_backend.py").write_text(BACKEND, encoding="utf-8")


def _external_wheel(
    tmp_path: Path, name: str, module: str | None, pool: Path, uv: str,
    env: dict[str, str],
) -> None:
    package = tmp_path / ("artifact-" + name)
    source = package / "src"
    source.mkdir(parents=True)
    distribution = importlib.metadata.distribution(name)
    if module is None:
        assert distribution.files is not None
        for member in distribution.files:
            if (
                ".." in member.parts or member.parts[0].endswith(".dist-info")
                or "__pycache__" in member.parts or member.suffix in {".pyc", ".pth"}
            ):
                continue
            destination = source / member
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(str(distribution.locate_file(member)), destination)
    else:
        shutil.copytree(
            str(distribution.locate_file(module)), source / module,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    if name == "cffi":
        assert distribution.files is not None
        for member in distribution.files:
            if member.name.startswith("_cffi_backend") and member.suffix in {".pyd", ".so"}:
                shutil.copyfile(str(distribution.locate_file(member)), source / member.name)
    requirements = [
        value for value in distribution.requires or []
        if (marker := Requirement(value).marker) is None or marker.evaluate({"extra": ""})
    ]
    (package / "pyproject.toml").write_text(
        f'[project]\nname="{name}"\nversion="{distribution.version}"\n'
        f"dependencies={json.dumps(requirements)}\n"
        '[build-system]\nrequires=[]\nbuild-backend="offline_backend"\nbackend-path=["."]\n',
        encoding="utf-8",
    )
    _backend(package)
    _run([uv, "build", "--offline", "--wheel", "--out-dir", pool, package], env, tmp_path)


def _relay_artifacts(
    tmp_path: Path, payload: Path, pool: Path, uv: str, env: dict[str, str],
    *, missing_gateway: bool = False,
) -> None:
    repository = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(repository / "tools"))
    applications = importlib.import_module("application_sources")
    toml = importlib.import_module("tomllib")
    metadata = toml.loads((payload / "pyproject.toml").read_text())
    selector = applications.application_sources(metadata)["fleet-manager"]
    application = applications.canonical_application(repository, selector)
    package = tmp_path / "artifact-fleet-manager"
    shutil.copytree(application, package, ignore=shutil.ignore_patterns(
        ".venv", "__pycache__", "tests", "build", "dist", "*.egg-info",
    ))
    if missing_gateway:
        (package / "src" / "fleet_manager" / "gateway_service_connector.py").unlink()
    _backend(package)
    _run([uv, "build", "--offline", "--wheel", "--out-dir", pool, package], env, tmp_path)
    project = toml.loads((package / "pyproject.toml").read_text())["project"]
    pending: list[str] = list(project["optional-dependencies"]["service-gateway"])
    built: set[str] = set()
    while pending:
        requirement = Requirement(pending.pop())
        name = requirement.name.lower().replace("_", "-")
        if name.startswith("agent-") or name in built:
            continue
        if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
            continue
        built.add(name)
        distribution = importlib.metadata.distribution(name)
        assert distribution.version in requirement.specifier
        _external_wheel(tmp_path, name, None, pool, uv, env)
        pending.extend(
            item for item in distribution.requires or []
            if (marker := Requirement(item).marker) is None or any(
                marker.evaluate({"extra": extra})
                for extra in ("", *requirement.extras)
            )
        )


def _closed_wheels(
    payload: Path, env: dict[str, str], home: Path, desired: str,
) -> tuple[Path, Path, Path, Path]:
    from packaging.markers import default_environment
    source_wheels = importlib.import_module("runtime_source_wheels")

    uv = shutil.which("uv")
    assert uv
    pool = payload.parents[1] / "artifacts"
    for package in [payload, *(payload / "libs").iterdir()]:
        if (package / "pyproject.toml").is_file():
            _run([uv, "build", "--offline", "--wheel", "--out-dir", pool, package], env, home)
    wheels: dict[str, dict[str, Any]] = {}
    for path in pool.glob("*.whl"):
        with zipfile.ZipFile(path) as archive:
            metadata = Parser().parsestr(archive.read(next(
                name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
            )).decode())
        name = re.sub(r"[-_.]+", "-", str(metadata["Name"])).lower()
        wheels[name] = {
            "name": name, "version": str(metadata["Version"]), "filename": path.name,
            "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
            "requires_python": metadata.get("Requires-Python", ""),
            "requires_dist": metadata.get_all("Requires-Dist", []),
            "provides_extras": metadata.get_all("Provides-Extra", []),
        }
    extras = ["federated-relay", "ssh"] if desired == "ssh-federated-relay" else [desired]
    roots = ["agent-vault[" + ",".join(extras) + "]==" + wheels["agent-vault"]["version"]]
    environment = default_environment()
    wanted: dict[str, set[str]] = source_wheels._extras(
        wheels, roots, environment, time.monotonic() + 30,
    )
    document = {
        "schema": "copilot-extensions.exact-runtime-profile", "version": 1,
        "release": {"identity": "fixture-release", "sha256": "sha256:" + "2" * 64},
        "mode": "source", "artifact": None, "component": "agent-vault", "backend": "cpu",
        "approval_source": "fixture-authority", "acquisition_policy": "fixture-policy",
        "target": {"device": "cpu", "environment": environment, "tags": ["py3-none-any"]},
        "roots": roots,
        "wheels": [{**wheels[name], "selected_extras": sorted(selected)}
                   for name, selected in sorted(wanted.items())],
    }
    profile = pool.parent / "closed-profile.json"
    profile.write_text(json.dumps(document), encoding="utf-8")
    return profile, pool, Path(getattr(sys, "_base_executable")), Path(uv)


@contextmanager
def _artifact_index(pool: Path) -> Iterator[str]:
    wheels = {path.name: path.read_bytes() for path in pool.glob("*.whl")}

    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self) -> None:
            self.do_GET()

        def do_GET(self) -> None:
            body: bytes | None
            if self.path.startswith("/simple/"):
                name = self.path.removeprefix("/simple/").strip("/")
                files = [{
                    "filename": filename, "url": "/artifacts/" + filename,
                    "hashes": {"sha256": hashlib.sha256(body).hexdigest()},
                    "upload-time": "2020-01-01T00:00:00Z",
                } for filename, body in wheels.items()
                    if filename.split("-")[0].replace("_", "-").lower() == name]
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

        def log_message(self, *_args: object) -> None:
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
def native_inputs(tmp_path_factory: pytest.TempPathFactory) -> Iterator[NativeInputs]:
    yield from native_profile_inputs(tmp_path_factory)


def native_profile_inputs(
    tmp_path_factory: pytest.TempPathFactory, *, crypto: bool = True, relay: bool = False,
    missing_gateway: bool = False,
) -> Iterator[NativeInputs]:
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
        "PATH": str(Path(getattr(sys, "_base_executable")).parent) + os.pathsep + env["PATH"],
    })
    pool = tmp_path / "artifacts"
    pool.mkdir()
    _external_wheel(tmp_path, "psutil", "psutil", pool, uv, env)
    if crypto:
        _external_wheel(tmp_path, "pycryptodomex", "Cryptodome", pool, uv, env)
    if relay:
        _relay_artifacts(tmp_path, payload, pool, uv, env, missing_gateway=missing_gateway)
    for package in [payload, *(payload / "libs").iterdir()]:
        if (package / "pyproject.toml").is_file():
            _backend(package)
    env.pop("UV_NO_INDEX")
    with _artifact_index(pool) as index:
        env["UV_DEFAULT_INDEX"] = index
        yield payload, home, env, pwsh


RELAY_CONSUMER = r'''
import asyncio,json,pathlib,sys,time
import agent_vault,fleet_manager
from agent_vault import federated_relay
from credential_relay.sources.gh_auth import GhAuthSource
root=pathlib.Path(sys.argv[1])
assert pathlib.Path(agent_vault.__file__).is_relative_to(pathlib.Path(sys.prefix))
assert pathlib.Path(fleet_manager.__file__).is_relative_to(pathlib.Path(sys.prefix))
calls=0
async def github(self,action,fields,*,timeout):
    global calls
    calls+=1
    return "protocol=https\nhost=github.com\nusername=account-a\ntoken=synthetic-secret\n\n"
GhAuthSource.resolve=github
async def scenario():
    runtime=federated_relay.selected_relay()
    assert runtime is not None
    deadline=time.monotonic()+12
    try:
        while not (root/"stop").exists() and time.monotonic()<deadline:
            await runtime.tick()
            await asyncio.sleep(.02)
        assert (root/"stop").exists(), "installed relay consumer deadline"
        assert calls==2
    finally:
        await runtime.close()
    assert runtime.status()=={"state":"stopped","channel_attached":False}
asyncio.run(scenario())
print(json.dumps({"installed_relay":True,"requests":calls,"settled":True}))
'''


@pytest.mark.timeout(180)
@pytest.mark.parametrize("desired", ["federated-relay", "ssh-federated-relay"])
def test_native_relay_profile_uses_first_party_artifact_and_real_tls_consumer(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch, desired: str,
) -> None:
    helpers = importlib.import_module("..test_federated_relay_service", __package__)
    from fleet_manager.gateway_service_caller import ServiceCaller
    from fleet_manager.machine_identity import PROFILE

    with contextmanager(native_profile_inputs)(
        tmp_path_factory, crypto="ssh" in desired, relay=True,
    ) as (payload, home, env, pwsh):
        installer = payload / "scripts" / "install.ps1"
        env["AGENT_VAULT_INSTALL_PROFILE"] = desired
        _run(_no_service_provision_command(home, installer, pwsh), env, home)
        root = home / ".agent-vault"
        selected = (root / "current-version").read_text().strip()
        assert selected.endswith("+" + desired)
        slot = root / "versions" / selected
        before = {str(p.relative_to(slot)): (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in slot.rglob("*") if p.is_file()}
        env.pop("AGENT_VAULT_INSTALL_PROFILE")
        _run(_no_service_provision_command(home, installer, pwsh, action="update"), env, home)
        assert {str(p.relative_to(slot)): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in slot.rglob("*") if p.is_file()} == before
        manifest = json.loads((root / "deploy-manifest.json").read_text())
        assert manifest["runtime_profile"] == desired and manifest["runtime_version"] == selected
        assert json.loads((root / "runtime-profile.json").read_text())["profile"] == desired
        assert json.loads((slot / ".runtime-input-origins.json").read_text())["extras"] == (
            ["federated-relay", "ssh"] if "ssh" in desired else ["federated-relay"]
        )
        python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if "ssh" in desired:
            _run([python, "-I", "-B", "-c", CONSUMER, home], env, home)
        else:
            _run([python, "-I", "-c",
                  "import importlib.util; assert importlib.util.find_spec('Cryptodome') is None"],
                 env, home)

        async def scenario() -> None:
            rig = helpers.Rig(home)
            rig.h.document["version"] = 4
            rig.h.document["identity"] = {
                "profile": PROFILE, "audience": helpers.MACHINE_AUDIENCE,
                "trust_valid_until": rig.h.clock.value + 3600,
            }
            rig.h.document["enrollment"] = {"mappings": [
                {"principal": name, "kind": kind, "resource": "service-a",
                 "key_source": name, "expires_at": rig.h.clock.value + 600}
                for name, kind in (("service-a", "service"), ("caller-a", "caller"))
            ]}
            await rig.start()
            try:
                async with helpers.conduit(rig.origin) as (route, _):
                    helpers.configure(root, rig, route, monkeypatch, machine_auth=True)
                    worker = asyncio.create_task(asyncio.to_thread(
                        _run, [python, "-I", "-B", "-c", RELAY_CONSUMER, root], env, home,
                    ))
                    try:
                        await helpers.eventually(
                            lambda: bool(rig.state.service_offers("vault-available-v1")), 5,
                        )
                        caller = ServiceCaller(
                            gateway_origin=rig.origin, proof_key=rig.h.keys[3], gateway_ca=rig.ca,
                            machine_principal="caller-a", machine_audience=helpers.MACHINE_AUDIENCE,
                            machine_resource="service-a",
                        )
                        leases: list[str] = []
                        for _ in range(2):
                            response = await caller.request(
                                "POST", "/api/source-credentials",
                                request_headers=(("X-Agent-Index-Installation-Id", "vault-test"),),
                                body=json.dumps(helpers.FIELDS | {
                                    "capability": helpers.CAPABILITY,
                                    "lease_contract": "credential-relay.account-source-lease/v1",
                                }).encode(),
                            )
                            assert response.status == 200
                            assert isinstance(response.body, bytes)
                            body = json.loads(response.body)
                            assert body["token"] == "synthetic-secret"
                            assert 0 < body["lease_remaining_seconds"] <= 60
                            leases.append(body["lease_id"])
                        assert leases[0] != leases[1]
                    finally:
                        (root / "stop").write_text("stop")
                        result = await worker
                    assert json.loads(result.stdout) == {
                        "installed_relay": True, "requests": 2, "settled": True,
                    }
                    await helpers.eventually(
                        lambda: not rig.state.service_offers("vault-available-v1"),
                    )
                    assert rig.backends[0].requests == []
            finally:
                await rig.close()

        asyncio.run(scenario())


@pytest.mark.timeout(180)
@pytest.mark.parametrize("desired", ["federated-relay", "ssh-federated-relay"])
def test_native_closed_profile_preserves_desire_and_checks_installed_dependencies(
    tmp_path_factory: pytest.TempPathFactory, desired: str,
) -> None:
    with contextmanager(native_profile_inputs)(
        tmp_path_factory, crypto="ssh" in desired, relay=True,
    ) as (payload, home, env, pwsh):
        wheels = _closed_wheels(payload, env, home, desired)
        env["PATH"] = str(home)
        assert all(shutil.which(name, path=env["PATH"]) is None
                   for name in ("python", "python3", "py"))
        installer = payload / "scripts" / "install.ps1"
        command = _no_service_provision_command(home, installer, pwsh, wheel_inputs=wheels)
        refused = subprocess.run(
            command, env=env, cwd=home, capture_output=True, text=True,
            timeout=30, **agent_procutil.no_window_kwargs(),
        )
        assert refused.returncode != 0
        assert "selected Vault dependency profile" in refused.stdout + refused.stderr
        root = home / ".agent-vault"
        assert not (root / "runtime-profile.json").exists()
        assert not (root / "current-version").exists()
        env["AGENT_VAULT_INSTALL_PROFILE"] = desired
        _run(command, env, home)
        selected = (root / "current-version").read_text().strip()
        assert ".profile." in selected and not selected.endswith("+" + desired)
        slot = root / "versions" / selected
        python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        callback = "ssh_dependency_health" if "ssh" in desired else "dependency_health"
        _run([python, "-I", "-B", "-c",
              f"from agent_vault.federated_relay import {callback}; {callback}()"], env, home)
        env.pop("AGENT_VAULT_INSTALL_PROFILE")
        _run(_no_service_provision_command(
            home, installer, pwsh, action="update", wheel_inputs=wheels,
        ), env, home)
        assert (root / "current-version").read_text().strip() == selected
        manifest = json.loads((root / "deploy-manifest.json").read_text())
        assert manifest["runtime_profile"] == desired
        assert manifest["runtime_version"] == selected
        assert json.loads((root / "runtime-profile.json").read_text())["profile"] == desired
        _run([sys.executable, "-I", "-B", payload / "scripts" / "runtime_profile.py",
              "--root", root, "current", "--package-version", manifest["source"]["version"]],
             env, home)
        assert json.loads((slot / ".runtime-input-origins.json").read_text())["extras"] == (
            ["federated-relay", "ssh"] if "ssh" in desired else ["federated-relay"]
        )
        assert not (root / "config.json").exists()


@pytest.mark.timeout(180)
def test_native_closed_profile_health_failure_never_publishes(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    with contextmanager(native_profile_inputs)(
        tmp_path_factory, crypto=False, relay=True, missing_gateway=True,
    ) as (payload, home, env, pwsh):
        wheels = _closed_wheels(payload, env, home, "federated-relay")
        env["AGENT_VAULT_INSTALL_PROFILE"] = "federated-relay"
        result = subprocess.run(
            _no_service_provision_command(
                home, payload / "scripts" / "install.ps1", pwsh, wheel_inputs=wheels,
            ),
            env=env, cwd=home, capture_output=True, text=True, timeout=120,
            **agent_procutil.no_window_kwargs(),
        )
        assert result.returncode != 0
        assert "gateway_service_connector" in result.stdout + result.stderr
        root = home / ".agent-vault"
        assert not (root / "current-version").exists()
        assert not (root / "deploy-manifest.json").exists()
        assert not list((root / "versions").glob("*/.install-complete.json"))


@pytest.fixture(scope="class")
def native_sequence(tmp_path_factory: pytest.TempPathFactory) -> Iterator[ProfileSequence]:
    with contextmanager(native_profile_inputs)(tmp_path_factory) as inputs:
        sequence = ProfileSequence(inputs)
        yield sequence
        assert sequence.complete, "select the complete native profile sequence"


class TestNativeProfileSequence:
    @pytest.mark.timeout(180)
    def test_base_repeat_and_ssh_switch(self, native_sequence: ProfileSequence) -> None:
        native_sequence.selected = _exercise_native_profile(*native_sequence.inputs)

    @pytest.mark.timeout(180)
    def test_ssh_repeat_update_and_preservation(self, native_sequence: ProfileSequence) -> None:
        assert native_sequence.selected is not None, "profile switch must run first"
        _exercise_native_profile_upgrade(*native_sequence.inputs, native_sequence.selected)
        native_sequence.complete = True


@pytest.mark.timeout(180)
def test_native_profile_failed_dependency_preserves_installed_consumer(
    native_inputs: NativeInputs,
) -> None:
    payload, home, env, pwsh = native_inputs
    installer = payload / "scripts" / "install.ps1"
    env["AGENT_VAULT_INSTALL_PROFILE"] = "ssh"
    _run(_no_service_provision_command(home, installer, pwsh), env, home)
    root = home / ".agent-vault"
    selected = (root / "current-version").read_text().strip()
    python_member = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    python = root / "versions" / selected / python_member
    _run([python, "-I", "-B", "-c", CONSUMER, home], env, home)
    _refuse_missing_dependency(payload, home, env, pwsh, python)


@pytest.fixture(scope="class", params=["ssh", "legacy-base"])
def source_profile_sequence(
    tmp_path_factory: pytest.TempPathFactory, request: pytest.FixtureRequest,
) -> Iterator[SourceProfileSequence]:
    with contextmanager(native_profile_inputs)(
        tmp_path_factory, crypto=request.param == "ssh",
    ) as inputs:
        sequence = SourceProfileSequence(inputs, request.param)
        yield sequence
        assert sequence.complete, "select the complete source-profile sequence"


class TestSourceProfileSequence:
    @pytest.mark.timeout(120)
    def test_source_stamp_and_first_provision(
        self, source_profile_sequence: SourceProfileSequence, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source_profile_sequence.selected = _exercise_source_stamp(
            *source_profile_sequence.inputs, source_profile_sequence.profile, monkeypatch,
        )

    @pytest.mark.timeout(120)
    def test_reprovision_without_publisher_and_refuse_changed_helper(
        self, source_profile_sequence: SourceProfileSequence,
    ) -> None:
        assert source_profile_sequence.selected is not None, "source stamp must run first"
        _exercise_source_reprovision(
            *source_profile_sequence.inputs, source_profile_sequence.profile,
            source_profile_sequence.selected,
        )
        source_profile_sequence.complete = True


def _exercise_source_stamp(
    payload: Path, home: Path, env: dict[str, str], pwsh: str, profile: str,
    monkeypatch: pytest.MonkeyPatch,
) -> SourceProfileState:
    plugin = Path(__file__).resolve().parents[2]
    repository = plugin.parents[1]
    monkeypatch.syspath_prepend(str(repository / "tools"))
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
    print("source-profile: stamp starting", flush=True)
    _run(_no_service_provision_command(home, installer, pwsh, action="stamp"),
         env, home, capture=False)
    print("source-profile: stamp completed", flush=True)
    root = home / ".agent-vault"
    snapshot = Path((root / "payload-dir").read_text().strip())
    assert (root / "stamped-version").read_text().strip() == identity.runtime_version
    assert not (root / "current-version").exists()
    print("source-profile: first provision starting", flush=True)
    _run(_no_service_provision_command(home, installer, pwsh), env, home, capture=False)
    print("source-profile: first provision completed", flush=True)
    selected = (
        source.runtime_version(version + "+ssh", identity.source_commit, digest)
        if profile == "ssh" else identity.runtime_version
    )
    slot = root / "versions" / selected
    return SourceProfileState(
        root, snapshot, slot, selected, version, identity.runtime_version, identity.document(),
    )


def _exercise_source_reprovision(
    payload: Path, home: Path, env: dict[str, str], pwsh: str, profile: str,
    state: SourceProfileState,
) -> None:
    root, snapshot, slot, selected = state.root, state.snapshot, state.slot, state.selected
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
    assert manifest["source"]["version"] == state.version and manifest["runtime_version"] == selected
    assert manifest["runtime_profile"] == ("ssh" if profile == "ssh" else "base")
    assert manifest["runtime_source"] == state.identity
    receipt = json.loads((root / "versions" / selected / "runtime-profile.json").read_text())
    assert receipt["runtimeSource"] == state.identity
    assert (root / "stamped-version").read_text().strip() == state.source_version
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


def _exercise_native_profile(
    payload: Path, home: Path, env: dict[str, str], pwsh: str,
) -> ProfileState:
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
    _run([slot / python_member, "-I", "-B", "-c", CONSUMER, home], env, home)
    return root, installer, base_slot, slot, selected, python_member


def _exercise_native_profile_upgrade(
    payload: Path, home: Path, env: dict[str, str], pwsh: str, state: ProfileState,
) -> None:
    root, installer, base_slot, slot, selected, python_member = state
    completed = {str(p.relative_to(slot)): (p.read_bytes(), p.stat().st_mtime_ns)
                 for p in slot.rglob("*") if p.is_file()}
    env.pop("AGENT_VAULT_INSTALL_PROFILE")
    _run(_no_service_provision_command(home, installer, pwsh), env, home)
    assert (root / "current-version").read_text().strip() == selected
    assert {str(p.relative_to(slot)): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in slot.rglob("*") if p.is_file()} == completed
    manifest = json.loads((root / "deploy-manifest.json").read_text())
    assert manifest["runtime_profile"] == "ssh" and manifest["runtime_version"] == selected
    _run([slot / python_member, "-I", "-B", "-c", CONSUMER, home], env, home)
    preserved = {
        str(path.relative_to(home)): (path.read_bytes(), path.stat().st_mtime_ns)
        for directory in ("keys", "protected-cache", "protected-keks")
        for path in (home / directory).rglob("*")
        if path.is_file() and not path.name.endswith(".lock")
    }
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
    assert {
        member: ((home / member).read_bytes(), (home / member).stat().st_mtime_ns)
        for member in preserved
    } == preserved


def _refuse_missing_dependency(
    payload: Path, home: Path, env: dict[str, str], pwsh: str, installed_python: Path,
) -> None:
    root = home / ".agent-vault"
    installer = payload / "scripts" / "install.ps1"
    incumbent = (root / "current-version").read_bytes()
    project = payload / "pyproject.toml"
    version = json.loads((root / "deploy-manifest.json").read_text())["source"]["version"]
    project.write_text(
        project.read_text().replace(f'version = "{version}"', 'version = "10.0.0"')
        .replace('ssh = ["pycryptodomex>=3.23.0"]', 'ssh = ["missing-profile-artifact==1.0"]'),
    )
    failed = subprocess.run(
        _no_service_provision_command(home, installer, pwsh, action="update"),
        env=env, cwd=home, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=120, **agent_procutil.no_window_kwargs(),
    )
    assert failed.returncode != 0 and "missing-profile-artifact" in failed.stdout + failed.stderr
    assert (root / "current-version").read_bytes() == incumbent
    _run([
        installed_python, "-I", "-B", "-c",
        "import runpy,sys; from pathlib import Path; runtime=runpy.run_path(sys.argv[1]); "
        "assert not runtime['is_complete'](Path(sys.argv[2]), '10.0.0+ssh')",
        payload / "scripts" / "versioned_runtime.py", root,
    ], env, home)
    assert json.loads((root / "runtime-profile.json").read_text())["profile"] == "ssh"
    _run([installed_python, "-I", "-B", "-c", CONSUMER, home], env, home)
