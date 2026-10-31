import asyncio
import json

import pytest

from agent_ssh_service.config import ServiceConfig, SSHServiceError
from agent_ssh_service.gateway import SSHService
from agent_ssh_service.hosting import registered_lifetime
from agent_ssh_service.relay import authorization, native_config, verify_host_pin
from agent_ssh_service.store import DirectoryStore
from fleet_manager.planning import canonical, decode

from conftest import enrollment, key
from test_notifications import request


def relay_document(config):
    value = config.to_dict()
    root = value["data_dir"]
    from pathlib import Path

    value["relay"] = {
        "sshd": str(Path(root) / "sshd"),
        "ssh_keygen": str(Path(root) / "ssh-keygen"),
        "host_key_file": str(Path(root) / "host-key"),
        "host_public_key": key(50), "listen_host": "127.0.0.1",
        "port": 22222, "user": "owner",
    }
    return value


def join(index=0):
    return {**enrollment(index), "relay_public_key": key(30 + index)}


@pytest.mark.parametrize("comment", ["", " owner@disposable", " comment with spaces"])
def test_derived_host_pin_compares_identity_not_openssh_comment(comment):
    verify_host_pin(key(50) + comment + "\n", key(50))


@pytest.mark.parametrize("derived", [
    "", "comment only", key(51), key(50).replace("ssh-ed25519", "ssh-rsa"),
    key(50) + "\n" + key(51), key(50) + "\ncomment",
])
def test_derived_host_pin_refuses_wrong_identity_or_multiple_lines(derived):
    with pytest.raises(SSHServiceError, match="native-relay-host-pin-mismatch"):
        verify_host_pin(derived, key(50))


def test_native_projection_is_forward_only_and_separates_peer_key_roles(config, tmp_path):
    selected = ServiceConfig.from_dict(relay_document(config))
    store = DirectoryStore(selected)
    store.enroll("principal-worker-a", join())
    store.enroll("principal-worker-b", join(1))
    compiled = authorization(store.relay_snapshot()).decode()
    assert key(1) not in compiled and key(2) not in compiled
    lines = compiled.splitlines()
    assert len(lines) == 2
    assert all(line.count("permitlisten=") == 1 for line in lines)
    assert all(line.count("permitopen=") == 2 for line in lines)
    assert 'permitlisten="127.0.0.1:24001"' in lines[0]
    assert 'permitlisten="127.0.0.1:24002"' not in lines[0]
    assert "restrict,port-forwarding," in lines[0]
    content = native_config(selected.to_dict()["relay"], tmp_path).decode()
    for expected in (
        "MaxSessions 0", "PermitTTY no", "GatewayPorts no",
        "AllowStreamLocalForwarding no", "AllowAgentForwarding no", "UsePAM no",
    ):
        assert expected + "\n" in content
    assert "Include " not in content and "Subsystem " not in content


@pytest.mark.parametrize("mutation", [
    "login-key", "another-login-key", "another-relay-key", "missing", "rotation",
])
def test_invalid_relay_enrollment_rolls_back_the_original_journal(config, mutation):
    store = DirectoryStore(ServiceConfig.from_dict(relay_document(config)))
    store.enroll("principal-worker-a", join())
    selected = join(1)
    if mutation == "login-key":
        selected["relay_public_key"] = selected["public_key"]
    elif mutation == "another-login-key":
        selected["relay_public_key"] = key(1)
    elif mutation == "another-relay-key":
        selected["relay_public_key"] = key(30)
    elif mutation == "missing":
        selected.pop("relay_public_key")
    else:
        selected = {**join(), "request_id": "changed-relay", "relay_public_key": key(41)}
    before = store.snapshot(), store.relay_snapshot()
    with pytest.raises(SSHServiceError):
        store.enroll("principal-worker-a" if mutation == "rotation" else "principal-worker-b",
                     selected)
    assert (store.snapshot(), store.relay_snapshot()) == before


def test_target_key_cannot_alias_an_existing_relay_key(config):
    store = DirectoryStore(ServiceConfig.from_dict(relay_document(config)))
    store.enroll("principal-worker-a", join())
    with pytest.raises(SSHServiceError, match="distinct"):
        store.enroll("principal-worker-b", {**join(1), "public_key": key(30)})
    assert len(store.relay_snapshot()) == 1


def test_generation_change_removes_old_relay_key_without_rotating_on_retry(config):
    value = relay_document(config)
    selected = ServiceConfig.from_dict(value)
    store = DirectoryStore(selected)
    store.enroll("principal-worker-a", join())
    value["revision"] = 2
    value["bindings"][0]["generation"] = 2
    replacement = DirectoryStore(ServiceConfig.from_dict(value))
    assert replacement.relay_snapshot() == []
    replacement.enroll("principal-worker-a", {
        **join(), "generation": 2, "request_id": "generation-two", "relay_public_key": key(45),
    })
    assert replacement.relay_snapshot()[0]["relay_public_key"] == key(45)
    assert DirectoryStore(ServiceConfig.from_dict(value)).relay_snapshot() == replacement.relay_snapshot()


@pytest.mark.parametrize("direction", ["inbound", "outbound"])
def test_unsupported_native_direction_refuses_instead_of_widening_it(config, direction):
    value = relay_document(config)
    value["policy"]["machines"][0][direction] = False
    with pytest.raises(SSHServiceError, match="bidirectional-policy"):
        ServiceConfig.from_dict(value)


def test_native_policy_cannot_change_under_the_same_config_revision(config):
    value = relay_document(config)
    DirectoryStore(ServiceConfig.from_dict(value))
    value["relay"]["host_public_key"] = key(51)
    with pytest.raises(SSHServiceError, match="configuration-revision-conflict"):
        DirectoryStore(ServiceConfig.from_dict(value))


def test_enabling_relay_does_not_adopt_legacy_enrollment_without_a_new_generation(config):
    store = DirectoryStore(config)
    before, _ = store.enroll("principal-worker-a", enrollment())
    value = relay_document(config)
    value["revision"] = 2
    with pytest.raises(SSHServiceError, match="relay-public-key-required"):
        DirectoryStore(ServiceConfig.from_dict(value))
    assert store.snapshot() == before


async def test_committed_join_retries_native_convergence_under_the_same_request(config):
    store = DirectoryStore(ServiceConfig.from_dict(relay_document(config)))
    calls = []

    def reconcile():
        calls.append(store.relay_snapshot())
        if len(calls) == 1:
            raise OSError("synthetic local writer failure")

    service = SSHService(store, reconcile_relay=reconcile)
    selected = request("principal-worker-a", "POST", "/enroll", canonical(join()))
    first = await service.handle(selected)
    assert first.status == 503 and decode(first.body)["error"] == "native-relay-unavailable"
    assert store.snapshot()["directory"]["revision"] == 2
    second = await service.handle(selected)
    assert second.status == 200 and decode(second.body)["directory"]["revision"] == 2
    assert calls[0] == calls[1]
    # Public notifications retain the existing shape; relay keys are not login keys.
    assert "relay_public_key" not in json.dumps(decode(second.body))


async def test_registration_waits_for_native_custody_and_drains_both_lifetimes():
    stop, native_ready, registered, drained = (
        asyncio.Event(), asyncio.Event(), asyncio.Event(), asyncio.Event(),
    )

    class Relay:
        async def run(self, selected_stop, ready):
            await native_ready.wait()
            ready.set()
            await selected_stop.wait()
            await asyncio.sleep(0.01)
            drained.set()

    class Connector:
        async def run(self, selected_stop):
            assert native_ready.is_set()
            registered.set()
            await selected_stop.wait()

    owner = asyncio.create_task(registered_lifetime(Connector(), Relay(), stop))
    try:
        await asyncio.sleep(0.01)
        assert not registered.is_set()
        native_ready.set()
        await asyncio.wait_for(registered.wait(), 1)
        stop.set()
        await asyncio.wait_for(owner, 1)
        assert drained.is_set()
    finally:
        native_ready.set()
        stop.set()
        await asyncio.gather(owner, return_exceptions=True)


async def test_native_failure_withdraws_the_existing_gateway_channel():
    stop, registered, drained = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Relay:
        async def run(self, selected_stop, ready):
            ready.set()
            await registered.wait()
            raise SSHServiceError("synthetic-native-exit")

    class Connector:
        async def run(self, selected_stop):
            registered.set()
            await selected_stop.wait()
            drained.set()

    with pytest.raises(SSHServiceError, match="synthetic-native-exit"):
        await asyncio.wait_for(registered_lifetime(Connector(), Relay(), stop), 1)
    assert drained.is_set()
