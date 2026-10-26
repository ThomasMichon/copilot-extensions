from copy import deepcopy

import pytest

from agent_ssh_service import approval
from agent_ssh_service.config import ServiceConfig, SSHServiceError
from agent_ssh_service.gateway import SSHService
from agent_ssh_service.store import DirectoryStore
from fleet_manager.gateway_service_channel import BODY_BYTES, TransportError
from fleet_manager.gateway_service_handler import ServiceResponse
from fleet_manager.planning import canonical
from fleet_manager.ssh_approval import EnrollmentResult, PeerEnrollmentClient

from conftest import key
from test_approval import decision, selected, submit
from test_approval_client import Caller


def full_fleet(config):
    value = deepcopy(config.to_dict())
    value["controller"] = "c" * 64
    value["policy"]["controllers"] = [value["controller"]]
    prototype = value["policy"]["machines"][0]
    machines, bindings, requests = [], [], []
    for index in range(32):
        machine = f"{index:02d}" + "m" * 126
        principal = f"{index:02d}" + "p" * 62
        machines.append({**prototype, "machine": machine, "controller": value["controller"]})
        bindings.append({
            "machine": machine, "environment": "linux", "principal": principal,
            "generation": 2**63 - 1, "alias": f"{index:02d}" + "a" * 126,
            "user": "u" * 64, "reverse_port": 24001 + index,
        })
        requests.append({
            "request_id": f"{index:02d}" + "q" * 126,
            "machine": machine, "environment": "linux", "generation": 2**63 - 1,
            "public_key": key(index + 1), "host_key": key(index + 33),
            "relay_public_key": key(index + 65), "enrollment_policy": "peer-approval",
        })
    value["bindings"] = bindings
    value["policy"]["machines"] = machines
    value["enrollment_policy"] = {
        "mode": "peer-approval", "recovery_principals": ["r" * 64],
    }
    return ServiceConfig.from_dict(value), requests


def test_near_limit_directory_growth_cannot_strand_a_committed_outcome(config):
    config, requests = full_fleet(config)
    store = DirectoryStore(config)
    bindings = config.to_dict()["bindings"]
    first, _ = store.enroll(bindings[0]["principal"], requests[0])
    activated, _ = store.approval.decide(
        "r" * 64, decision(first, confirm=True, reason=chr(0x10FFFF) * 512), recovery=True,
    )
    initial_bytes = canonical(activated)
    for binding, request in zip(bindings[1:], requests[1:]):
        pending, _ = store.enroll(binding["principal"], request)
        store.approval.decide(bindings[0]["principal"], decision(pending))
    directory = store.snapshot()
    directory_bytes = canonical(directory)
    assert len(directory["directory"]["machines"]) == 32
    assert len(directory_bytes) <= BODY_BYTES
    oversized = canonical({**activated, "directory": directory})
    assert len(oversized) > BODY_BYTES
    with pytest.raises(TransportError, match="response-limit"):
        ServiceResponse(200, (), oversized)
    status = DirectoryStore(config).approval.status(
        bindings[0]["principal"], requests[0]["request_id"])
    assert canonical(status) == initial_bytes
    assert len(ServiceResponse(200, (), canonical(status)).body) <= BODY_BYTES
    EnrollmentResult.parse(canonical(status))
    repeated, changed = store.approval.decide(
        "r" * 64, decision(first, confirm=True, reason=chr(0x10FFFF) * 512), recovery=True,
    )
    assert not changed and repeated == activated


def test_receipt_capacity_refusal_rolls_back_activation_and_decision(config, monkeypatch):
    store = DirectoryStore(selected(config))
    pending = submit(store)
    before = store.snapshot()
    with monkeypatch.context() as capacity:
        capacity.setattr(approval, "BODY_BYTES", 1)
        with pytest.raises(SSHServiceError, match="enrollment-response-limit"):
            store.approval.decide(
                "recovery-owner", decision(pending, confirm=True, reason="first-peer"),
                recovery=True,
            )
    assert store.snapshot() == before
    assert store.approval.status("principal-worker-a", pending["request_id"]) == pending


async def test_maximum_fleet_pending_pages_fit_and_keep_every_candidate_reachable(config):
    config, requests = full_fleet(config)
    store = DirectoryStore(config)
    bindings = config.to_dict()["bindings"]
    for binding, request in zip(bindings, requests):
        store.enroll(binding["principal"], request)
    service = SSHService(store)
    recovery = PeerEnrollmentClient(Caller(service, "r" * 64))
    seen, after, pages = [], None, 0
    try:
        while True:
            page = await recovery.pending(after=after)
            assert len(ServiceResponse(200, (), canonical(page)).body) <= BODY_BYTES
            seen.extend(item["candidate"]["requester"] for item in page["requests"])
            pages += 1
            if page["next"] is None:
                break
            assert after is None or page["next"] > after
            after = page["next"]
            assert pages <= len(bindings)
        assert pages > 1
        assert seen == sorted(binding["principal"] for binding in bindings)
        assert len(set(seen)) == 32
        own = await PeerEnrollmentClient(Caller(
            service, bindings[0]["principal"])).pending()
        assert [item["candidate"]["requester"] for item in own["requests"]] == [seen[0]]
        assert own["next"] is None
    finally:
        await service.close()


async def test_terminal_pending_page_cursor_is_counted_at_actual_transport_limit(config):
    config, requests = full_fleet(config)
    value = config.to_dict()
    for index, binding in enumerate(value["bindings"]):
        binding["principal"] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"[index]
    store = DirectoryStore(ServiceConfig.from_dict(value))
    bindings = value["bindings"]
    items = []
    for binding, request in zip(bindings, requests):
        candidate = store.approval._candidate(binding["principal"], request)
        items.append({
            "request_id": request["request_id"], "candidate": candidate,
            "fingerprint": store.approval._fingerprint(candidate),
        })
        if len(canonical({
            "schema": "agent-ssh.enrollment-pending", "version": 1,
            "requests": items, "next": None,
        })) > BODY_BYTES:
            break
    full = {
        "schema": "agent-ssh.enrollment-pending", "version": 1,
        "requests": items, "next": None,
    }
    excess = len(canonical(full)) - BODY_BYTES - 1
    assert excess >= 0
    for item, request in zip(items, requests):
        trim = min(excess, len(request["request_id"]) - 1)
        request["request_id"] = request["request_id"][:len(request["request_id"]) - trim]
        item["request_id"] = request["request_id"]
        excess -= trim
    assert excess == 0 and len(canonical(full)) == BODY_BYTES + 1
    assert len(canonical({**full, "next": items[-1]["candidate"]["requester"]})) == BODY_BYTES
    for binding, request in zip(bindings, requests[:len(items)]):
        store.enroll(binding["principal"], request)
    service = SSHService(store)
    client = PeerEnrollmentClient(Caller(service, "r" * 64))
    try:
        first = await client.pending()
        assert first["next"] is not None
        second = await client.pending(after=first["next"])
        assert second["next"] is None
        for page in (first, second):
            assert len(ServiceResponse(200, (), canonical(page)).body) <= BODY_BYTES
        actual = first["requests"] + second["requests"]
        assert actual == items
    finally:
        await service.close()


async def test_oversized_padded_recovery_reason_refuses_before_transport(config):
    service = SSHService(DirectoryStore(full_fleet(config)[0]))
    caller = Caller(service, "r" * 64)
    try:
        with pytest.raises(TransportError, match="explicit-ssh-recovery"):
            await PeerEnrollmentClient(caller).recover(
                "p", "request-a", "SHA256:" + "A" * 43,
                confirm=True, reason=" " * 513 + "first-peer")
        assert caller.calls == []
    finally:
        await service.close()
