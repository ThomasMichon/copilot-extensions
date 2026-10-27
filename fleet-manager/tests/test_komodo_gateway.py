"""Original TLS caller custody reaches the fixed owner without a service connector."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import sys
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from fleet_manager.komodo_control import gateway_binding
from fleet_manager.komodo_declaration import KomodoDeclaration
from fleet_manager.komodo_owner import KomodoOwner
from fleet_manager.komodo_selection import KomodoSelection
from fleet_manager.runtime_admission import AdmissionAuthority, OperationGrant
from gateway_service_transport_fixtures import Rig
from test_gateway_enrolled_identity import EnrolledRig
from test_gateway_local_handler import local_binding
from test_komodo_declaration import intent
from test_komodo_native import native_rig as native_rig
from test_runtime_admission import actor, policy

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux host substrate contract")


@pytest.mark.parametrize("profile", ["jwt-pop-v1", "enrolled-pop-v1"])
@pytest.mark.parametrize("lost_reply", [False, True])
def test_real_tls_original_caller_and_current_operation_policy_reach_native_owner(
    native_rig: Any, profile: str, lost_reply: bool,
) -> None:
    rig = native_rig
    (rig["directory"] / "stateful").touch()
    runtime = rig["root"] / "host-runtime"
    runtime.mkdir(mode=0o700)
    for name in ("core", "periphery"):
        (rig["material"].keys / (name + ".pub")).write_text("fixture-public-key-" + name)

    async def exercise() -> None:
        root = rig["root"] / "gateway"
        root.mkdir()
        gateway = EnrolledRig(root) if profile == "enrolled-pop-v1" else Rig(root)

        async def health(request: web.Request) -> web.Response:
            if request.path == "/auth/login/LoginLocalUser":
                assert await request.json() == {
                    "username": "admin_username-fixture-material",
                    "password": "admin_password-fixture-material",
                }
                return web.json_response({"data": {"jwt": "fixture-local-token"}})
            assert request.headers.get("authorization") == "fixture-local-token"
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
        app.router.add_post("/{tail:.*}", health)
        async with TestServer(app, host="127.0.0.1") as server:
            desired = rig["selected"].to_dict()
            desired.update(core_port=server.port, outfitting_seconds=10)
            now = gateway.h.clock.value
            selected = KomodoSelection(
                KomodoDeclaration.from_dict(desired), "fleet-a", "install-a", 1,
                "policy-1", now + 600, "e" * 64, runtime, rig["tooling"], rig["material"])
            original = intent(deployment=selected.deployment_digest)
            current = [policy(
                valid_from=now - 1, valid_until=now + 600, identity_trust_until=now + 600,
                identity_profile=profile, enrollments=(actor(
                    subject="caller-a", operations=(OperationGrant(
                        "manager.converge", "host-a", 1, original.request_digest),)),))]
            admission = AdmissionAuthority(snapshot=lambda: current[0], clock=gateway.h.clock)
            owner = KomodoOwner(current=lambda: selected, admission=admission,
                                authority="gateway-a", clock=gateway.h.clock)
            slot = local_binding(gateway, lambda _: None).slot
            binding = gateway_binding(owner, slot, role="client", installation="client-a")
            if lost_reply:
                (rig["directory"] / "lost-reply").touch()
            try:
                await gateway.start(
                    identity_provider=gateway.h.provider if profile == "enrolled-pop-v1" else None,
                    local_handlers=(binding,))
                for attempt in range(2):
                    async with await gateway.request(
                        "/api/converge", "POST", data=original.encoded,
                        headers={"X-Identity": "forged", "Cookie": "principal=forged"},
                    ) as response:
                        result = await response.json()
                        if lost_reply and attempt == 0:
                            assert response.status == 409 and result["error"] == "komodo-convergence-refused"
                        else:
                            assert response.status == 200 and result["status"] == "succeeded", result
                async with await gateway.request(
                    "/api/status", "POST", data=original.encoded,
                ) as response:
                    assert response.status == 200
                    assert (await response.json())["converged"] is True
                async with await gateway.request(
                    "/api/converge", "POST", data=intent(deployment="f" * 64).encoded,
                ) as response:
                    assert response.status == 403
                    assert (await response.json())["error"] == "forbidden-scope"
                current[0] = replace(current[0], revision=2, enrollments=())
                async with await gateway.request(
                    "/api/converge", "POST", data=original.encoded,
                ) as response:
                    assert response.status == 403
                    assert (await response.json())["error"] == "unknown-enrollment"
                assert (rig["directory"] / "effects").read_text() == "one\n"
                assert not admission._issued and not gateway.state.channels and not gateway.connectors
            finally:
                await gateway.close()

    asyncio.run(exercise())
