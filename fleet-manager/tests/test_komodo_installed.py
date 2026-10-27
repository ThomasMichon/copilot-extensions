"""Fresh noneditable host SDK, denied checkout reads and actual bootstrap consumption."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from test_continuity_installed import sdk_runtime as sdk_runtime
from test_komodo_native import native_rig as native_rig

pytestmark = [
    pytest.mark.client_distribution_full,
    pytest.mark.skipif(sys.platform != "linux", reason="Linux host substrate contract"),
]

PROBE = r"""
import json, os, runpy, sys, time
from pathlib import Path
context = json.loads(sys.argv[1])
checkout = Path(context["checkout"]).resolve()
runtime = Path(context["runtime"]).resolve()
def deny_source(event, arguments):
    if event in {"open", "os.listdir", "os.scandir"} and arguments:
        value = arguments[0]
        if isinstance(value, (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(value)).resolve()
            if path.is_relative_to(checkout) and not path.is_relative_to(runtime):
                raise PermissionError("Komodo SDK refused checkout access")
sys.addaudithook(deny_source)
try:
    runpy.run_path(str(checkout / "fleet-manager/src/fleet_manager/komodo_owner.py"))
except PermissionError:
    pass
else:
    raise AssertionError("source read control did not refuse")
for site in context["sites"]:
    assert Path(site).resolve().is_relative_to(runtime)
    sys.path.append(site)
from importlib import metadata
for distribution in metadata.distributions():
    assert Path(distribution.locate_file("")).resolve().is_relative_to(runtime)
    direct = distribution.read_text("direct_url.json")
    assert not direct or not json.loads(direct).get("dir_info", {}).get("editable")
    assert distribution.files
    for entry in distribution.files:
        assert Path(distribution.locate_file(entry)).resolve().is_relative_to(runtime)
import hashlib
import fleet_manager, agent_procutil
for module in (fleet_manager, agent_procutil):
    assert Path(module.__file__).resolve().is_relative_to(runtime)
package = Path(fleet_manager.__file__).parent
for name, expected in context["expected"].items():
    assert hashlib.sha256((package / name).read_bytes()).hexdigest() == expected

from agent_procutil.owned_process import run_caller_owned
from fleet_manager.komodo_control import CONTRACT, bootstrap
from fleet_manager.komodo_declaration import KomodoDeclaration
from fleet_manager.komodo_native import KomodoTooling
from fleet_manager.komodo_owner import KomodoOwner
from fleet_manager.komodo_profile import KomodoMaterialization
from fleet_manager.komodo_selection import KomodoSelection
from fleet_manager.machine_identity import (
    MachineBinding, MachineIdentityVerifier, PROFILE, make_machine_proof,
)
from fleet_manager.operations import OperationIntent
from fleet_manager.planning import canonical
from fleet_manager.runtime_admission import (
    AdmissionAuthority, AdmissionError, AuthoritySnapshot, Enrollment, OperationGrant,
)
from joserfc.jwk import ECKey

now = int(time.time())
clock = lambda: int(time.time())
material = KomodoMaterialization(**{key: Path(value) for key, value in context["material"].items()})
def native(argv, **kwargs):
    return run_caller_owned([
        context["native_python"], context["native_script"], *argv[1:],
    ], **kwargs)
tooling = KomodoTooling(
    Path(context["native_python"]), Path(context["client_config"]), "daemon-a", native)
selection = KomodoSelection(
    KomodoDeclaration.from_dict(context["declaration"]), "fleet-a", "install-a", 1,
    "policy-1", now + 600, "e" * 64, Path(context["host_runtime"]), tooling, material)
request = OperationIntent.from_dict({
    "schema": "fleet-manager.operation-intent", "version": 1,
    "operation": "installed-bootstrap", "authority": "gateway-a",
    "principal": "actor-a", "account": "account-a", "target": "host-a", "generation": 1,
    "command": "manager.converge", "parameters": {"deployment": selection.deployment_digest},
})
key = ECKey.generate_key("P-256")
binding = MachineBinding(
    "operator-a", "caller", "bootstrap", canonical(key.as_dict(private=False)), now + 600)
verifier = MachineIdentityVerifier(
    audience="manager-owner", bindings=(binding,), trust_valid_until=now + 600, clock=clock)
proof = make_machine_proof(
    key, principal="operator-a", audience="manager-owner", kind="caller", resource="bootstrap",
    method="POST", target_uri="https://manager.example/komodo/converge",
    nonce=verifier.nonce, body=request.encoded, now=now)
identity = verifier.verify(
    "operator-a", proof, method="POST", target_uri="https://manager.example/komodo/converge",
    kind="caller", resource="bootstrap", content_digest=request.request_digest)
snapshot = AuthoritySnapshot(
    "gateway-a", "fleet-a", PROFILE, "manager-owner", 1, now - 1, now + 600, now + 600,
    (Enrollment(
        "operator-a", "actor-a", "account-a", "operator", "client", "client-a", 1, "active",
        operations=(OperationGrant(
            "manager.converge", "host-a", 1,
            "0" * 64 if context["mode"] == "changed-grant" else request.request_digest),),
    ),), identity_profile=PROFILE)
admission = AdmissionAuthority(snapshot=lambda: snapshot, clock=clock)
owner = KomodoOwner(current=lambda: selection, admission=admission, authority="gateway-a", clock=clock)
try:
    receipt = bootstrap(owner, identity, request, role="client", installation="client-a")
except AdmissionError as error:
    assert context["mode"] == "changed-grant" and error.code == "forbidden-scope"
    print(json.dumps({"result": "refused-before-native", "source_denied": True, "contract": CONTRACT}))
else:
    assert context["mode"] == "admitted" and receipt.to_dict()["status"] == "succeeded"
    print(json.dumps({"result": "installed-owner-succeeded", "source_denied": True, "contract": CONTRACT}))
"""


@pytest.mark.parametrize("mode", ["changed-grant", "admitted"])
def test_noneditable_host_bootstrap_uses_common_verified_identity_and_fixed_native_owner(
    tmp_path: Path, sdk_runtime: Any, native_rig: Any, mode: str,
) -> None:
    from agent_procutil.owned_process import run_caller_owned

    rig = native_rig
    (rig["directory"] / "stateful").touch()
    runtime = rig["root"] / "host-runtime"
    runtime.mkdir(mode=0o700)
    for name in ("core", "periphery"):
        (rig["material"].keys / (name + ".pub")).write_text("fixture-public-key-" + name)

    async def exercise() -> None:
        desired = rig["selected"].to_dict()

        async def handler(request: web.Request) -> web.Response:
            assert (rig["directory"] / "live.json").exists()
            if request.path == "/auth/login/LoginLocalUser":
                assert await request.json() == {
                    "username": "admin_username-fixture-material",
                    "password": "admin_password-fixture-material",
                }
                return web.json_response({"data": {"jwt": "installed-owner-fixture-token"}})
            assert request.headers.get("authorization") == "installed-owner-fixture-token"
            return web.json_response({
                "/read/GetVersion": {"version": "2.0.0"},
                "/read/GetServer": {
                    "id": "server-a", "name": "example-host", "config": {"enabled": True},
                    "info": {"public_key": "fixture-public-key-periphery"},
                },
                "/read/GetServerState": {"status": "Ok"},
                "/read/GetPeripheryInformation": {
                    "version": "2.0.0", "public_key": "fixture-public-key-periphery",
                    "docker_connected": True,
                },
            }[request.path])

        app = web.Application()
        app.router.add_post("/{tail:.*}", handler)
        async with TestServer(app, host="127.0.0.1") as server:
            desired.update(core_port=server.port, outfitting_seconds=10)
            context = json.loads(sdk_runtime[1][-1])
            context.update(
                mode=mode, declaration=desired, host_runtime=str(runtime),
                client_config=str(rig["tooling"].client_config),
                native_python=str(Path(sys.executable).resolve()),
                native_script=str(rig["root"] / "docker-fixture.py"),
                material={name: str(getattr(rig["material"], name))
                          for name in rig["material"].__dataclass_fields__},
            )
            result = await asyncio.to_thread(
                run_caller_owned,
                [str(sdk_runtime[0]), "-I", "-S", "-c", PROBE, json.dumps(context)],
                cwd=str(tmp_path), timeout=30, max_stdout_bytes=65536, max_stderr_bytes=65536)
            assert result.returncode == 0, result.stderr
            observed = json.loads(result.stdout)
            assert observed["source_denied"] is True
            assert observed["contract"] == "fleet-manager.komodo-substrate-owner.v1"
            effects = rig["directory"] / "effects"
            if mode == "changed-grant":
                assert observed["result"] == "refused-before-native" and not effects.exists()
                assert not (rig["directory"] / "calls.jsonl").exists()
            else:
                assert observed["result"] == "installed-owner-succeeded"
                assert effects.read_text() == "one\n"

    asyncio.run(exercise())
