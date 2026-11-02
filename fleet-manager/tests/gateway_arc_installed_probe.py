"""Finite installed-SDK consumer; only synthetic owner-selected fixture inputs."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import time

from cryptography.hazmat.primitives.serialization import load_pem_private_key
import jwt

from agent_procutil.owned_process import run_caller_owned
from fleet_manager.arc_owner import ArcDeployment, ArcHelmOwner, ArcTooling
from fleet_manager.gateway_identity_provider import IdentityProvider, IdentityTrust
from fleet_manager.gateway_local_handler import LocalHandlerBinding
from fleet_manager.gateway_manifest import GatewayManifest
from fleet_manager.gateway_service_caller import ServiceCaller
from fleet_manager.gateway_service_server import Listener, STATE, start
from fleet_manager.loadout_preparation import PreparationStore
from fleet_manager.node_journal import NodeJournal
from fleet_manager.operations import OperationIntent
from fleet_manager.planning import canonical
from fleet_manager.runtime_admission import (
    AdmissionAuthority, AuthoritySnapshot, Enrollment, OperationGrant,
)


async def consume(root: Path, installed: Path) -> None:
    fixture = json.loads((root / "consumer.json").read_bytes())
    now, profile, trust = fixture["now"], fixture["profile"], fixture["trust"]
    request = OperationIntent((root / "operation.json").read_bytes())
    selected = ArcDeployment(
        (root / "selection.json").read_bytes(), (root / "selected-chart.tgz").read_bytes())
    grant = OperationGrant("runner.converge", "worker-a", 1, request.request_digest)
    enrollment = Enrollment(
        "caller-a", "actor-a", "account-a", "operator", "client", "client-a", 1, "active",
        operations=(grant,))
    current = AuthoritySnapshot(
        "gateway-a", "example", trust["issuer"], trust["audience"], 1, now - 1,
        now + 600, now + 600, (enrollment,), identity_profile=profile)
    def clock():
        return int(time.time())

    admission = AdmissionAuthority(snapshot=lambda: current, clock=clock)
    journal = NodeJournal(
        root / "node.db", authority="gateway-a", installation="install-a", target="worker-a")

    def native(argv, **kwargs):
        return run_caller_owned(
            [sys.executable, str(root / "helm-fixture.py"), *argv[1:]], **kwargs)

    tooling = ArcTooling(
        Path(sys.executable).absolute(), root / "kubeconfig", "cluster-a", root, native)
    owner = ArcHelmOwner(
        tooling=tooling,
        preparations=PreparationStore(root / "preparation.db", fleet="example",
                                      installation="install-a"),
        journal=journal, admission=admission, current=lambda: selected, clock=clock)
    policy = json.loads((root / "policy.json").read_bytes())
    slot = GatewayManifest.parse(canonical(policy["manifest"])).slots[0]
    provider = None
    if profile == "enrolled-pop-v1":
        def prepare(raw):
            assert json.loads(raw) == {"snapshot": "selected"}
            return IdentityTrust(
                trust["issuer"], trust["audience"], canonical(trust["jwks"]),
                trust["trust_valid_until"])
        provider = IdentityProvider("example-provider", prepare)
    listener = Listener("example", "public-gateway", "127.0.0.1", 0, "https://localhost:0",
                        str(root / "tls.pem"), str(root / "tls-key.pem"))
    request_headers = (
        ("Accept", "application/json"), ("Content-Type", "application/json"),
        ("If-Match", '"current"'),
    )
    arc = owner.gateway_binding(slot, role="client", installation="client-a")

    async def handle(local):
        assert all(type(pair) is tuple and len(pair) == 2 for pair in local.request.headers)
        assert dict(local.request.headers) == {
            name.lower(): value for name, value in request_headers}
        return await arc.handle(local)

    serving = await start(
        listener, root / "policy.json", clock=clock, identity_provider=provider,
        local_handlers=(LocalHandlerBinding(slot, handle),))
    assert serving.app[STATE].status().roles[0].state == "registered-local"
    issuer = load_pem_private_key((root / "test-issuer.pem").read_bytes(), password=None)
    proof = load_pem_private_key((root / "test-caller.pem").read_bytes(), password=None)

    async def access():
        claims = {
            "iss": trust["issuer"], "aud": trust["audience"], "iat": clock(), "exp": clock() + 300,
            "sub": "issuer-caller-a" if provider else "caller-a",
        }
        if provider:
            claims["client_id"] = "client-caller-a"
        else:
            claims["cnf"] = {"jkt": fixture["proof_key"]}
        return jwt.encode(claims, issuer, algorithm="ES256",
                          headers={"typ": "at+jwt", "kid": "issuer-1"})

    caller = ServiceCaller(
        gateway_origin=serving.origin, gateway_ca=str(root / "tls.pem"), proof_key=proof,
        access_token_callback=access)
    try:
        expected = "unknown" if fixture["lost_reply"] else "succeeded"
        for attempt in range(2):
            response = await caller.request(
                "POST", "/api/converge", body=request.encoded, request_headers=request_headers)
            value = json.loads(response.body)
            if fixture["lost_reply"] and attempt == 0:
                assert response.status == 502 and value == {"error": "outcome-unknown"}
            else:
                assert response.status == 200 and value["status"] == expected
                assert value["request_digest"] == request.request_digest
        commands = [json.loads(line) for line in
                    (root / "commands.jsonl").read_text().splitlines()]
        assert sum(command[0] == "upgrade" for command in commands) == 1
        assert journal.inspect(request).to_dict()["status"] == expected
        assert not serving.app[STATE].channels and not admission._issued
        changed = request.to_dict()
        changed["parameters"]["deployment"] = "f" * 64
        response = await caller.request(
            "POST", "/api/converge", body=OperationIntent.from_dict(changed).encoded,
            request_headers=request_headers)
        assert response.status == 403
        assert json.loads(response.body) == {"error": "forbidden-scope"}
        assert not admission._issued
        for name, module in tuple(sys.modules.items()):
            if name == "fleet_manager" or name.startswith("fleet_manager."):
                assert Path(module.__file__).resolve().is_relative_to(installed)
    finally:
        await serving.close()


if __name__ == "__main__":
    asyncio.run(consume(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()))
    print("installed-arc-cell-passed")
