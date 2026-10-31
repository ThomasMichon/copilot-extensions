import asyncio
from copy import deepcopy

import pytest

from agent_ssh_service.config import SSHServiceError
from agent_ssh_service.gateway import SSHService
from agent_ssh_service.store import DirectoryStore
from fleet_manager.gateway_service_channel import TransportError
from fleet_manager.gateway_service_handler import ServiceResponse
from fleet_manager.planning import canonical, decode
from fleet_manager.ssh_approval import EnrollmentResult, PeerEnrollmentClient
from fleet_manager.ssh_directory import SSHDirectoryClient

from conftest import enrollment
from test_approval import bootstrap, decision, selected, submit
from test_notifications import request


class Caller:
    def __init__(self, service, principal):
        self.service, self.principal = service, principal
        self.calls = []

    async def request(self, method, target, *, body=b"", response_limit=None):
        self.calls.append((method, target, body))
        return await self.service.handle(request(
            self.principal, method, target.removeprefix("/ssh"), body,
        ))


def client(service, principal):
    return PeerEnrollmentClient(Caller(service, principal))


async def test_sdk_requires_explicit_recovery_then_exact_peer_approval(config):
    service = SSHService(DirectoryStore(selected(config)))
    first, second = (client(service, "principal-worker-" + name) for name in ("a", "b"))
    recovery = client(service, "recovery-owner")
    try:
        pending = (await first.submit(enrollment())).to_dict()
        with pytest.raises(TransportError):
            await first.approve(**decision(pending))
        assert not any(path.endswith("/recover") for _, path, _ in first.caller.calls)
        active = await recovery.recover(
            **decision(pending), confirm=True, reason="first-peer")
        assert active.to_dict()["status"] == "active"
        pending = (await second.submit(enrollment(1))).to_dict()
        listed = (await first.pending())["requests"]
        assert listed[0]["fingerprint"] == pending["fingerprint"]
        approved = await first.approve(**decision(pending))
        repeated = await first.approve(**decision(pending))
        assert repeated.encoded == approved.encoded
        assert (await second.status(pending["request_id"])).encoded == approved.encoded
        assert (await first.pending())["requests"] == []
    finally:
        await service.close()


async def test_old_directory_client_cannot_silently_bypass_selected_approval(config):
    service = SSHService(DirectoryStore(selected(config)))
    caller = Caller(service, "principal-worker-a")
    try:
        with pytest.raises(TransportError):
            await SSHDirectoryClient(caller).enroll(enrollment())
        assert service.store.snapshot()["directory"]["machines"] == []
        assert service.store.approval.pending("principal-worker-a")["requests"] == []
    finally:
        await service.close()


@pytest.mark.parametrize("legacy", [False, True])
async def test_sdk_checks_running_policy_before_any_effectful_request(config, legacy):
    service = SSHService(DirectoryStore(config))

    class PriorCaller(Caller):
        async def request(self, method, target, **kwargs):
            if legacy:
                self.calls.append((method, target, b""))
                return ServiceResponse(404)
            return await super().request(method, target, **kwargs)

    caller = PriorCaller(service, "principal-worker-a")
    try:
        with pytest.raises(TransportError, match="ssh-peer-approval"):
            await PeerEnrollmentClient(caller).submit(enrollment())
        assert [(method, path) for method, path, _ in caller.calls] == [
            ("GET", "/ssh/enrollment")]
        assert service.store.snapshot()["directory"]["machines"] == []
    finally:
        await service.close()


async def test_provider_rollback_after_discovery_refuses_required_policy(config):
    service = SSHService(DirectoryStore(config))

    class ChangedCaller(Caller):
        async def request(self, method, target, **kwargs):
            if method == "GET":
                self.calls.append((method, target, b""))
                return ServiceResponse(200, (), canonical({
                    "schema": "agent-ssh.enrollment-contract", "version": 1,
                    "policy": "peer-approval", "peer_approval": True,
                }))
            return await super().request(method, target, **kwargs)

    try:
        with pytest.raises(TransportError):
            await PeerEnrollmentClient(ChangedCaller(
                service, "principal-worker-a")).submit(enrollment())
        assert service.store.snapshot()["directory"]["machines"] == []
    finally:
        await service.close()


@pytest.mark.parametrize("metadata", [
    {},
    {"schema": "agent-ssh.enrollment-contract", "version": 2,
     "policy": "peer-approval", "peer_approval": True},
    {"schema": "agent-ssh.enrollment-contract", "version": 1,
     "policy": "peer-approval", "peer_approval": 1},
])
async def test_sdk_unknown_or_invalid_contract_does_not_submit(config, metadata):
    service = SSHService(DirectoryStore(config))

    class UnsupportedCaller(Caller):
        async def request(self, method, target, **kwargs):
            self.calls.append((method, target, b""))
            return ServiceResponse(200, (), canonical(metadata))

    caller = UnsupportedCaller(service, "principal-worker-a")
    try:
        with pytest.raises((TransportError, ValueError)):
            await PeerEnrollmentClient(caller).submit(enrollment())
        assert [method for method, _, _ in caller.calls] == ["GET"]
        assert service.store.snapshot()["directory"]["machines"] == []
    finally:
        await service.close()


async def test_sdk_missing_recovery_confirmation_does_not_contact_a_service(config):
    service = SSHService(DirectoryStore(selected(config)))
    recovery = client(service, "recovery-owner")
    try:
        with pytest.raises(TransportError, match="explicit-ssh-recovery"):
            await recovery.recover(
                "principal-worker-a", "join-worker-a", "SHA256:" + "A" * 43,
                confirm=False, reason="first-peer",
            )
        assert recovery.caller.calls == []
    finally:
        await service.close()


async def test_pending_submission_does_not_wake_directory_or_reconcile_relay(config):
    store = DirectoryStore(selected(config))
    original = bootstrap(store)
    calls = []
    service = SSHService(store, reconcile_relay=lambda: calls.append("active"))
    revision = original["decision"]["directory_revision"]
    waiting = asyncio.create_task(service.handle(request(
        "principal-worker-a", "GET", f"/directory?after={revision}&wait=5")))
    try:
        pending = (await client(service, "principal-worker-b").submit(enrollment(1))).to_dict()
        await asyncio.sleep(0.02)
        assert not waiting.done() and calls == []
        await client(service, "principal-worker-a").approve(**decision(pending))
        notice = await asyncio.wait_for(waiting, 2)
        assert notice.status == 200
        assert decode(notice.body)["directory"]["revision"] == revision + 1
        assert calls == ["active"]
    finally:
        if not waiting.done():
            waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
        await service.close()


async def test_relay_failure_keeps_committed_decision_and_original_retry(config):
    store = DirectoryStore(selected(config))
    bootstrap(store)
    pending = submit(store, 1)
    calls = []

    def reconcile():
        calls.append("attempt")
        if len(calls) == 1:
            raise SSHServiceError("fixture-write-unavailable")

    service = SSHService(store, reconcile_relay=reconcile)
    approver = client(service, "principal-worker-a")
    revision = store.snapshot()["directory"]["revision"]
    waiting = asyncio.create_task(service.handle(request(
        "principal-worker-a", "GET", f"/directory?after={revision}&wait=5")))
    try:
        await asyncio.sleep(0.02)
        with pytest.raises(TransportError):
            await approver.approve(**decision(pending))
        assert not waiting.done()
        committed = await client(service, "principal-worker-b").status(pending["request_id"])
        assert committed.to_dict()["status"] == "active"
        repeated = await approver.approve(**decision(pending))
        assert repeated.encoded == committed.encoded
        assert calls == ["attempt", "attempt"]
        notice = await asyncio.wait_for(waiting, 2)
        assert decode(notice.body)["directory"]["revision"] == revision + 1
    finally:
        if not waiting.done():
            waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
        await service.close()


@pytest.mark.parametrize("mutation", ["candidate", "pending-decision", "pending-directory"])
def test_sdk_rejects_inconsistent_fingerprint_or_pending_admission(config, mutation):
    pending = deepcopy(submit(DirectoryStore(selected(config))))
    if mutation == "candidate":
        pending["candidate"]["binding"]["alias"] = "different"
    elif mutation == "pending-decision":
        pending["decision"] = {"method": "peer", "principal": "other"}
    else:
        pending["directory"] = {}
    with pytest.raises((TransportError, ValueError)):
        EnrollmentResult.parse(canonical(pending))
