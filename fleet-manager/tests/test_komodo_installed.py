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
from komodo_public_fixtures import PUBLIC_DER, PUBLIC_PEM

from test_continuity_installed import sdk_runtime as sdk_runtime
from test_continuity_installed import baseline_sdk_runtime as baseline_sdk_runtime
from test_komodo_native import native_rig as native_rig
from komodo_installed_repair_probe import REPAIR_PROBE, UNSUPPORTED_PROBE

pytestmark = [
    pytest.mark.client_distribution_full,
    pytest.mark.skipif(sys.platform != "linux", reason="Linux host substrate contract"),
]

SDK_GUARD = r"""
import json, os, runpy, sys, time
from pathlib import Path
context = json.loads(sys.argv[1])
checkout = Path(context["checkout"]).resolve()
runtime = Path(context["runtime"]).resolve()
denied_sources = [Path(value).resolve() for value in context["denied_sources"]]
def deny_source(event, arguments):
    if event in {"open", "os.listdir", "os.scandir"} and arguments:
        value = arguments[0]
        if isinstance(value, (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(value)).resolve()
            if any(path.is_relative_to(source) for source in denied_sources) and not path.is_relative_to(runtime):
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
"""

PROBE = SDK_GUARD + r"""
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
from fleet_manager.planning import PlanError, canonical
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
    "policy-1", context.get("valid_until", now + 600),
    context.get("candidate_release", "e" * 64), Path(context["host_runtime"]), tooling, material)
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
except PlanError as error:
    assert context["mode"] == "retain-original" and "current Core version" in str(error), str(error)
    from fleet_manager.node_journal import NodeJournal
    journal = NodeJournal(
        Path(context["host_runtime"]) / selection.resource / "node.sqlite",
        authority="gateway-a", installation="install-a", target="host-a")
    retained = journal.inspect(request).to_dict()
    assert retained["status"] == "unknown" and retained["sequence"] == 2
    assert retained["result_ref"] is None
    print(json.dumps({
        "result": "original-unknown", "source_denied": True, "contract": CONTRACT,
        "request": request.to_dict(), "proof_key": identity.proof_key,
    }))
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
        (rig["material"].keys / (name + ".pub")).write_text(PUBLIC_PEM[name])

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
                    "id": "server-a", "name": "example-host", "config": {"enabled": True, "address": ""},
                    "info": {"public_key": PUBLIC_DER["periphery"]},
                },
                "/read/GetServerState": {"status": "Ok"},
                "/read/GetPeripheryInformation": {
                    "version": "2.0.0", "public_key": PUBLIC_DER["periphery"],
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


@pytest.mark.parametrize("mode", [
    "success", "changed-grant", "withdrawn-transition", "executor-skew",
    "save-after", "journal-after",
])
def test_installed_current_executor_repairs_retained_baseline_original_without_replay(
    tmp_path: Path, sdk_runtime: Any, baseline_sdk_runtime: Any, native_rig: Any, mode: str,
) -> None:
    from agent_procutil.owned_process import run_caller_owned
    from fleet_manager.planning import digest

    rig = native_rig
    (rig["directory"] / "stateful").touch()
    runtime = rig["root"] / "host-runtime"
    runtime.mkdir(mode=0o700)
    for name in ("core", "periphery"):
        (rig["material"].keys / (name + ".pub")).write_text(PUBLIC_PEM[name])
    sentinel = rig["material"].mongo_data / "retained-fixture"
    sentinel.write_text("original-data")
    old = json.loads(baseline_sdk_runtime[1][-1])
    current = json.loads(sdk_runtime[1][-1])
    candidate_release, executor_release = digest(old["expected"]), digest(current["expected"])
    assert candidate_release != executor_release
    health_failure = [True]

    async def exercise() -> None:
        async def handler(request: web.Request) -> web.Response:
            if request.path == "/auth/login/LoginLocalUser":
                assert await request.json() == {
                    "username": "admin_username-fixture-material",
                    "password": "admin_password-fixture-material",
                }
                return web.json_response({"data": {"jwt": "installed-repair-fixture-token"}})
            assert request.headers.get("authorization") == "installed-repair-fixture-token"
            return web.json_response({
                "/read/GetVersion": {"version": "9.0.0" if health_failure[0] else "2.0.0"},
                "/read/GetServer": {
                    "id": "server-a", "name": "example-host", "config": {"enabled": True, "address": ""},
                    "info": {"public_key": ""},
                },
                "/read/GetServerState": {"status": "Ok"},
                "/read/GetPeripheryInformation": {
                    "version": "2.0.0", "public_key": PUBLIC_DER["periphery"],
                    "docker_connected": True,
                },
            }[request.path])

        app = web.Application()
        app.router.add_post("/{tail:.*}", handler)
        async with TestServer(app, host="127.0.0.1") as server:
            import time

            desired = rig["selected"].to_dict()
            desired.update(core_port=server.port, outfitting_seconds=10)
            common = {
                "declaration": desired, "host_runtime": str(runtime), "valid_until": int(time.time()) + 600,
                "candidate_release": candidate_release, "executor_release": executor_release,
                "client_config": str(rig["tooling"].client_config),
                "native_python": str(Path(sys.executable).resolve()),
                "native_script": str(rig["root"] / "docker-fixture.py"),
                "material": {name: str(getattr(rig["material"], name))
                             for name in rig["material"].__dataclass_fields__},
            }

            async def probe(sdk: Any, body: str, context: dict[str, Any]) -> dict[str, Any]:
                result = await asyncio.to_thread(
                    run_caller_owned, [str(sdk[0]), "-I", "-S", "-c", body, json.dumps(context)],
                    cwd=str(tmp_path), timeout=30, max_stdout_bytes=65536, max_stderr_bytes=65536)
                assert result.returncode == 0, result.stderr
                return json.loads(result.stdout)

            original = await probe(baseline_sdk_runtime, PROBE, {**old, **common, "mode": "retain-original"})
            assert original["result"] == "original-unknown" and original["source_denied"] is True
            owner_roots = list(runtime.glob("*/selection.json"))
            assert len(owner_roots) == 1
            owner_root = owner_roots[0].parent
            original_state = owner_roots[0].read_bytes()
            original_profile = json.loads(original_state)["attempt"]["profile"]
            profile_bytes = Path(original_profile).read_bytes()
            native_before = (rig["directory"] / "live.json").read_bytes()
            journal_before = (owner_root / "node.sqlite").stat()
            calls_before = (rig["directory"] / "calls.jsonl").read_bytes()
            unsupported = await probe(
                baseline_sdk_runtime, SDK_GUARD + UNSUPPORTED_PROBE, {**old, **common})
            assert unsupported["result"] == "unsupported-installed-repair"
            assert (rig["directory"] / "calls.jsonl").read_bytes() == calls_before
            assert owner_roots[0].read_bytes() == original_state
            health_failure[0] = False
            repair_context = {
                **current, **common, "mode": mode, "original": original["request"],
                "original_proof_key": original["proof_key"],
            }
            result = await probe(sdk_runtime, SDK_GUARD + REPAIR_PROBE, repair_context)
            if mode in ("save-after", "journal-after"):
                assert result["result"] == "publication-interrupted"
                pending = json.loads(owner_roots[0].read_bytes())["good"]
                reference = owner_root / "receipts" / (digest(pending) + ".json")
                immutable = reference.read_bytes()
                assert (rig["directory"] / "effects").read_text() == "one\n"
                assert (rig["directory"] / "live.json").read_bytes() == native_before
                result = await probe(
                    sdk_runtime, SDK_GUARD + REPAIR_PROBE, {**repair_context, "resume": True})
                assert reference.read_bytes() == immutable
            assert result["source_denied"] is True and result["distinct_identity"] is True
            assert result["result"] == ("original-repaired" if mode in (
                "success", "save-after", "journal-after") else "repair-refused")
            if result["result"] == "repair-refused":
                assert owner_roots[0].read_bytes() == original_state
            assert result["baseline_contract"] == "fleet-manager.komodo-substrate-owner.v1"
            assert result["repair_contract"] == "fleet-manager.komodo-original-repair.v1"
            journal_after = (owner_root / "node.sqlite").stat()
            assert (journal_before.st_dev, journal_before.st_ino) == (journal_after.st_dev, journal_after.st_ino)
            assert Path(original_profile).read_bytes() == profile_bytes
            assert (rig["directory"] / "live.json").read_bytes() == native_before
            assert (rig["directory"] / "effects").read_text() == "one\n"
            assert not (rig["directory"] / "retire-effects").exists()
            assert sentinel.read_text() == "original-data"

    asyncio.run(exercise())
