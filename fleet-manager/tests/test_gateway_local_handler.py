"""Original cryptographic request admission, not a context-label effect grant."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import threading

import pytest

from fleet_manager.gateway_local_handler import LocalHandlerBinding, settled
from fleet_manager.gateway_manifest import GatewayManifest
from fleet_manager.gateway_service_handler import RequestDisconnected, ServiceResponse
from fleet_manager.gateway_service_policy import ServiceAuthority, ServicePolicyError
from fleet_manager.node_journal import NodeJournal
from fleet_manager.operations import OperationIntent
from fleet_manager.planning import canonical
from fleet_manager.runtime_admission import AdmissionAuthority, AdmissionError
from gateway_service_transport_fixtures import Rig, eventually
from test_gateway_enrolled_identity import EnrolledHarness, EnrolledRig
from test_gateway_service_policy import Harness
from test_runtime_admission import actor, intent, policy
from test_arc_owner import rig as rig
from test_runtime_identity import keys as keys


def operation_authority(harness):
    now = harness.clock.value
    current = [policy(
        valid_from=now - 1, valid_until=now + 3600, identity_trust_until=now + 3600,
        enrollments=(actor(subject="caller-a"),),
    )]
    return AdmissionAuthority(snapshot=lambda: current[0], clock=harness.clock), current


def authorize(harness, grant, admission, request=None, checkpoint=lambda: None):
    return harness.owner.authorize_operation(
        grant, request or intent(), admission, role="client", installation="client-a",
        authority="gateway-a", checkpoint=checkpoint)


def test_original_verified_identity_and_independent_actor_grant():
    h = Harness()
    h.owner.reload()
    admission, current = operation_authority(h)
    grant = h.caller(method="POST")
    permit = authorize(h, grant, admission)
    assert permit.enrollment.subject == "caller-a"
    assert permit.enrollment.principal == "actor-a"
    assert permit.request_digest == intent().request_digest
    permit = admission.revalidate(permit, intent())
    admission.complete(permit, intent())
    assert not admission._issued
    with pytest.raises(AdmissionError, match="identity-reused"):
        authorize(h, grant, admission)
    with pytest.raises(ServicePolicyError, match="invalid-grant"):
        authorize(h, replace(grant), admission)
    with pytest.raises(ServicePolicyError, match="method-denied"):
        authorize(h, h.caller(method="GET"), admission)
    with pytest.raises(AdmissionError, match="actor-mismatch"):
        authorize(h, h.caller(method="POST"), admission, intent(principal="forged"))
    current[0] = replace(current[0], revision=2, enrollments=())
    with pytest.raises(AdmissionError, match="unknown-enrollment"):
        authorize(h, h.caller(method="POST"), admission)
    assert not admission._issued


@pytest.mark.parametrize("withdraw", ["caller", "trust", "route", "request", "released"])
def test_each_permit_checkpoint_retains_gateway_and_request_custody(withdraw):
    h = Harness()
    h.owner.reload()
    admission, _ = operation_authority(h)
    grant = h.caller(method="POST")
    closed = [False]

    def checkpoint():
        if closed[0]:
            raise PermissionError("request-ended")

    permit = authorize(h, grant, admission, checkpoint=checkpoint)
    permit = admission.revalidate(permit, intent())
    if withdraw == "caller":
        h.document["callers"] = []
        h.update()
    elif withdraw == "trust":
        h.document["identity"]["audience"] = "changed"
        h.update()
    elif withdraw == "route":
        h.document["manifest"]["slots"][0]["routes"][0]["prefix"] = "/changed"
        h.update()
    elif withdraw == "released":
        h.owner.release_caller(grant)
    else:
        closed[0] = True
    with pytest.raises((ServicePolicyError, PermissionError)):
        admission.revalidate(permit, intent())
    assert not admission._issued


def test_failed_final_bound_checkpoint_never_issues_a_permit():
    h = Harness()
    h.owner.reload()
    admission, _ = operation_authority(h)
    calls = []

    def checkpoint():
        calls.append(1)
        if len(calls) == 2:
            h.owner.release_caller(grant)
            raise PermissionError("withdrawn")

    grant = h.caller(method="POST")
    with pytest.raises(PermissionError, match="withdrawn"):
        authorize(h, grant, admission, checkpoint=checkpoint)
    assert len(calls) == 2 and not admission._issued
    assert admission._evidence  # Denial cannot refund an already consumed proof.


def test_initial_bound_refusal_also_consumes_the_original_verified_evidence():
    h = Harness()
    h.owner.reload()
    admission, _ = operation_authority(h)
    grant = h.caller(method="POST")

    def checkpoint():
        raise PermissionError("withdrawn-before-policy")

    with pytest.raises(PermissionError, match="withdrawn-before-policy"):
        authorize(h, grant, admission, checkpoint=checkpoint)
    assert not admission._issued and admission._evidence
    with pytest.raises(AdmissionError, match="identity-reused"):
        authorize(h, grant, admission)


def test_final_bound_check_cannot_issue_after_operation_policy_expiry():
    h = Harness()
    h.owner.reload()
    admission, current = operation_authority(h)
    current[0] = replace(current[0], valid_until=h.clock.value + 1)
    calls = []

    def checkpoint():
        calls.append(1)
        if len(calls) == 2:
            h.clock.value = current[0].valid_until

    with pytest.raises(AdmissionError, match="stale-policy"):
        authorize(h, h.caller(method="POST"), admission, checkpoint=checkpoint)
    assert len(calls) == 2 and not admission._issued and admission._evidence


@pytest.mark.parametrize("method", ["revalidate", "complete"])
def test_final_bound_check_cannot_renew_or_complete_an_expired_permit(method):
    h = Harness()
    h.owner.reload()
    admission, _ = operation_authority(h)
    calls = []

    def checkpoint():
        calls.append(1)
        if len(calls) == 4:
            h.clock.value = permit.expires_at

    permit = authorize(h, h.caller(method="POST"), admission, checkpoint=checkpoint)
    with pytest.raises(AdmissionError, match="expired-permit"):
        getattr(admission, method)(permit, intent())
    assert len(calls) == 4 and not admission._issued


def test_actual_enrolled_caller_requires_independent_explicit_operation_profile():
    h = EnrolledHarness()
    h.owner.reload()
    admission, current = operation_authority(h)
    with pytest.raises(AdmissionError, match="unsupported-profile"):
        authorize(h, h.caller(method="POST"), admission)
    current[0] = replace(current[0], identity_profile="enrolled-pop-v1")
    admission = AdmissionAuthority(snapshot=lambda: current[0], clock=h.clock)
    grant = h.caller(method="POST")
    permit = authorize(h, grant, admission)
    assert permit.enrollment.subject == "caller-a"
    assert permit.expires_at <= grant.expires_at
    permit = admission.revalidate(permit, intent())
    h.document["enrollment"]["mappings"] = [
        item for item in h.document["enrollment"]["mappings"] if item["kind"] != "caller"]
    h.update()
    with pytest.raises(ServicePolicyError):
        admission.complete(permit, intent())
    assert not admission._issued


@pytest.mark.parametrize("field,value", [("fleet", "foreign-fleet"), ("authority", "foreign-owner")])
def test_operation_permit_must_match_the_gateway_fleet_and_registered_backend_owner(field, value):
    h = Harness()
    h.owner.reload()
    admission, current = operation_authority(h)
    current[0] = replace(current[0], **{field: value})
    request = intent(**({"authority": value} if field == "authority" else {}))
    with pytest.raises(ServicePolicyError, match="operation-owner-mismatch"):
        authorize(h, h.caller(method="POST"), admission, request)
    assert not admission._issued


def test_gateway_routing_identity_does_not_rename_the_registered_operation_authority():
    h = Harness()
    h.document["manifest"]["gateway"] = "public-gateway"
    h.owner = ServiceAuthority(
        fleet="fleet-a", gateway="public-gateway", source=h.source, clock=h.clock)
    h.owner.reload()
    admission, _ = operation_authority(h)
    permit = authorize(h, h.caller(method="POST"), admission)
    assert permit.authority == "gateway-a"
    permit = admission.revalidate(permit, intent())
    admission.complete(permit, intent())
    assert not admission._issued


def local_binding(rig, handler):
    rig.h.document["manifest"]["slots"][0]["routes"][0]["capabilities"] = ["http"]
    manifest = GatewayManifest.parse(canonical(rig.h.document["manifest"]))
    return LocalHandlerBinding(manifest.slots[0], handler)


def test_real_tls_local_owner_commits_and_replays_original_journal_operation(tmp_path):
    async def scenario():
        rig = Rig(tmp_path)
        admission, current = operation_authority(rig.h)
        journal = NodeJournal(
            tmp_path / "node.db", authority="gateway-a", installation="install-a", target="node-a")
        received = []
        effects = []

        async def handle(local):
            received.append(local)
            request = OperationIntent(local.request.body)
            try:
                permit = local.authorize_operation(
                    request, admission, authority=journal.authority,
                    role="client", installation="client-a")
            except AdmissionError as error:
                return ServiceResponse(403, (("content-type", "application/json"),),
                                       canonical({"error": error.code}))
            try:
                permit = admission.revalidate(permit, request)
                journal.accept(request)
                admission.complete(permit, request)

                def effect(connection):
                    connection.execute("CREATE TABLE IF NOT EXISTS sample(value INTEGER)")
                    connection.execute("INSERT INTO sample VALUES(1)")
                    effects.append(1)
                    return "result-a"

                receipt = journal.commit_cooperative(request, effect)
                return ServiceResponse(200, (("content-type", "application/json"),),
                                       receipt.encoded)
            finally:
                admission.discard(permit)

        binding = local_binding(rig, handle)
        try:
            await rig.start(local_handlers=(binding,))
            original = intent()
            for _ in range(2):
                async with await rig.request("/api/submit", "POST", data=original.encoded,
                                             headers={
                                                 "Accept": "application/json",
                                                 "Content-Type": "application/json",
                                                 "If-Match": '"current"',
                                                 "X-Identity": "forged",
                                                 "Cookie": "principal=forged",
                                             }) as response:
                    assert response.status == 200
                    assert (await response.json())["status"] == "succeeded"
                    assert response.headers["Cache-Control"] == "no-store"
            assert effects == [1]
            assert received[0].request.context.principal == "caller-a"
            for local in received[:2]:
                assert all(type(pair) is tuple and len(pair) == 2
                           for pair in local.request.headers)
                assert dict(local.request.headers) == {
                    "accept": "application/json", "content-type": "application/json",
                    "if-match": '"current"',
                }
            assert not rig.state.channels and not rig.connectors
            assert not admission._issued
            with pytest.raises(RequestDisconnected):
                received[0].checkpoint()
            async with await rig.request(
                "/api/submit", "POST", data=intent(account="foreign").encoded,
            ) as response:
                assert response.status == 403
                assert (await response.json())["error"] == "actor-mismatch"
            current[0] = None
            async with await rig.request("/api/submit", "POST", data=original.encoded) as response:
                assert response.status == 403
                assert (await response.json())["error"] == "authority-unavailable"
            assert effects == [1] and not admission._issued
            assert rig.state.pending == 0 and not rig.state.local_busy
        finally:
            await rig.close()
    asyncio.run(scenario())


def test_local_registration_reserves_slot_and_refuses_changed_desired_owner(tmp_path):
    async def scenario():
        rig = Rig(tmp_path)
        received = []

        async def handle(local):
            received.append(local)
            return ServiceResponse(200, body=b"ok")

        binding = local_binding(rig, handle)
        try:
            await rig.start(local_handlers=(binding,))
            assert rig.state.status().roles[0].state == "registered-local"
            assert rig.state.status().roles[0].generation is None
            async with await rig.request() as response:
                assert response.status == 200 and await response.read() == b"ok"
            async with rig.session.post(
                rig.origin + "/_fleet/service-admit",
                json={"slot": "service-a", "access": "not-a-credential", "proof": "not-a-proof"},
            ) as response:
                assert response.status == 401
            credentials = await rig.auth(
                "/_fleet/service-admit", "POST", subject="service-a", key=1)
            async with rig.session.post(
                rig.origin + "/_fleet/service-admit",
                json={"slot": "service-a", "access": credentials["Authorization"].split(" ", 1)[1],
                      "proof": credentials["DPoP"]},
            ) as response:
                assert response.status == 409
            rig.h.document["manifest"]["slots"][0]["configuration_ref"] = "changed@2"
            await rig.update()
            assert rig.state.status().roles[0].state == "unavailable"
            async with await rig.request() as response:
                assert response.status == 403
                assert (await response.json())["error"] == "local-owner-changed"
            assert len(received) == 1
            assert not rig.state.local_busy and rig.state.pending == 0
        finally:
            await rig.close()
    asyncio.run(scenario())


def test_local_handler_failure_is_unknown_not_retryable_success(tmp_path):
    async def scenario():
        rig = Rig(tmp_path)
        calls = []

        async def handle(local):
            calls.append(1)
            raise ValueError("must-not-reach-wire")

        try:
            await rig.start(local_handlers=(local_binding(rig, handle),))
            async with await rig.request("/api/submit", "POST", data=b"{}") as response:
                assert response.status == 502
                value = await response.json()
                assert value == {"error": "outcome-unknown"}
                assert "must-not-reach-wire" not in json.dumps(value)
            assert calls == [1] and rig.state.pending == 0 and not rig.state.local_busy
        finally:
            await rig.close()
    asyncio.run(scenario())


def test_local_binding_accepts_the_declared_awaitable_handler_contract(tmp_path):
    async def scenario():
        gateway = Rig(tmp_path)

        def handle(local):
            local.checkpoint()
            result = asyncio.get_running_loop().create_future()
            result.set_result(ServiceResponse(200, body=b"bounded-result"))
            return result

        try:
            await gateway.start(local_handlers=(local_binding(gateway, handle),))
            async with await gateway.request() as response:
                assert response.status == 200
                assert await response.read() == b"bounded-result"
            assert not gateway.state.local_active and not gateway.state.local_busy
        finally:
            await gateway.close()
    asyncio.run(scenario())


def test_client_loss_closes_local_custody_before_handler_cancellation(tmp_path):
    async def scenario():
        rig = Rig(tmp_path)
        started, stopped = asyncio.Event(), asyncio.Event()

        async def handle(local):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                with pytest.raises(RequestDisconnected):
                    local.checkpoint()
                stopped.set()

        pending = None
        try:
            await rig.start(local_handlers=(local_binding(rig, handle),))
            pending = asyncio.create_task(rig.request("/api/submit", "POST", data=b"{}"))
            await asyncio.wait_for(started.wait(), 3)
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            await asyncio.wait_for(stopped.wait(), 3)
            await eventually(lambda: rig.state.pending == 0)
            assert rig.state.pending == 0 and not rig.state.local_busy
        finally:
            if pending is not None:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            await rig.close()
    asyncio.run(scenario())


def test_local_owner_serializes_requests_and_releases_slot_after_completion(tmp_path):
    async def scenario():
        gateway = Rig(tmp_path)
        started, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def handle(local):
            calls.append(local)
            started.set()
            await release.wait()
            local.checkpoint()
            return ServiceResponse(200)

        pending = None
        try:
            await gateway.start(local_handlers=(local_binding(gateway, handle),))
            pending = asyncio.create_task(gateway.request("/api/submit", "POST"))
            await asyncio.wait_for(started.wait(), 3)
            async with await gateway.request("/api/submit", "POST") as response:
                assert response.status == 429
                assert await response.json() == {"error": "local-owner-busy"}
            assert len(calls) == 1 and gateway.state.pending == 1
            release.set()
            async with await pending as response:
                assert response.status == 200
            async with await gateway.request("/api/submit", "POST") as response:
                assert response.status == 200
            assert len(calls) == 2 and gateway.state.pending == 0
            assert not gateway.state.local_busy
        finally:
            release.set()
            if pending is not None:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            await gateway.close()
    asyncio.run(scenario())


def test_gateway_shutdown_closes_admission_and_joins_accepted_local_native_work(tmp_path):
    async def scenario():
        gateway = Rig(tmp_path)
        started, release, completed = threading.Event(), threading.Event(), threading.Event()
        requests = []

        async def handle(local):
            requests.append(local)

            def worker():
                started.set()
                assert release.wait(3)
                with pytest.raises(RequestDisconnected):
                    local.checkpoint()
                completed.set()
                return ServiceResponse(200)

            return await settled(worker)

        pending = closing = None
        try:
            await gateway.start(local_handlers=(local_binding(gateway, handle),))
            pending = asyncio.create_task(gateway.request("/api/submit", "POST"))
            await eventually(started.is_set)
            closing = asyncio.create_task(gateway.serving.close())
            await eventually(lambda: gateway.state.stopping)
            with pytest.raises(RequestDisconnected):
                requests[0].checkpoint()
            assert not closing.done() and not completed.is_set()
            release.set()
            await asyncio.wait_for(closing, 3)
            gateway.serving = None
            await eventually(lambda: gateway.state.pending == 0)
            assert completed.is_set() and not gateway.state.local_active
            assert not gateway.state.local_busy
        finally:
            release.set()
            if closing is not None:
                await asyncio.gather(closing, return_exceptions=True)
            if pending is not None:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            await gateway.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("rig", ["wall-clock-native"], indirect=True)
@pytest.mark.parametrize("profile", ["jwt-pop-v1", "enrolled-pop-v1"])
@pytest.mark.parametrize("lost_reply", [False, True])
def test_actual_tls_fixed_arc_operation_runs_native_owner_without_a_connector(
    tmp_path, rig, profile, lost_reply,
):
    async def scenario():
        assert rig["native_protocol"]
        gateway = EnrolledRig(tmp_path) if profile == "enrolled-pop-v1" else Rig(tmp_path)
        gateway.h.clock.value = rig["harness"].clock.value
        if profile == "enrolled-pop-v1":
            gateway.h.trust["trust_valid_until"] = gateway.h.clock.value + 3600
            for mapping in gateway.h.document["enrollment"]["mappings"]:
                mapping["expires_at"] = gateway.h.clock.value + 600
        else:
            gateway.h.document["identity"]["trust_valid_until"] = gateway.h.clock.value + 3600
        gateway.h.document["manifest"]["fleet"] = "example"
        gateway.listener = replace(gateway.listener, fleet="example")
        expected = local_binding(gateway, lambda _: None).slot
        authority = rig["harness"]
        authority.current = replace(
            authority.current,
            identity_profile=profile,
            enrollments=(actor(subject="caller-a",
                               operations=authority.current.enrollments[0].operations),))
        binding = rig["owner"].gateway_binding(
            expected, role="client", installation="client-a")
        request = rig["request"]()
        if lost_reply:
            (rig["root"] / "lost-reply").touch()
        try:
            await gateway.start(
                identity_provider=gateway.h.provider if profile == "enrolled-pop-v1" else None,
                local_handlers=(binding,))
            for attempt in range(2):
                async with await gateway.request(
                    "/api/converge", "POST", data=request.encoded,
                ) as response:
                    result = await response.json()
                    if lost_reply and attempt == 0:
                        assert response.status == 502
                        assert result == {"error": "outcome-unknown"}
                    else:
                        assert response.status == 200, result
                        assert result["status"] == ("unknown" if lost_reply else "succeeded")
            commands = [json.loads(line) for line in
                        (rig["root"] / "commands.jsonl").read_text().splitlines()]
            assert sum(command[0] == "upgrade" for command in commands) == 1
            assert rig["journal"].inspect(request).to_dict()["status"] == (
                "unknown" if lost_reply else "succeeded")
            assert not gateway.state.channels and not gateway.connectors
            assert not authority.owner._issued
            async with await gateway.request(
                "/api/converge", "POST", data=intent().encoded,
            ) as response:
                assert response.status == 400
                assert (await response.json())["error"] == "invalid-arc-operation"
            changed = request.to_dict()
            changed["parameters"]["deployment"] = "f" * 64
            async with await gateway.request(
                "/api/converge", "POST", data=OperationIntent.from_dict(changed).encoded,
            ) as response:
                assert response.status == 403
                assert (await response.json())["error"] == "forbidden-scope"
            assert not authority.owner._issued
        finally:
            await gateway.close()
    asyncio.run(scenario())
