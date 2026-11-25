import base64
from copy import deepcopy
import json
from pathlib import Path
import struct
import subprocess
import sys
from typing import Any

import pytest
from agent_procutil import no_window_kwargs

from agent_ssh.gateway_native import AdmissionUncertain, MAX_BYTES, NativeGateway


def key(number: int) -> str:
    wire = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + bytes([number]) * 32
    return "ssh-ed25519 " + base64.b64encode(wire).decode()


def peer(name: str = "receiver", number: int = 1) -> dict[str, Any]:
    return {"machine": name, "user": "owner", "public_key": key(number), "host_key": key(number + 10)}


class Provider:
    def __init__(self) -> None:
        self.descriptor: dict[str, Any] = {
            "schema": 1, "backend": "openssh-reverse", "machine": "receiver",
            "gateway_user": "owner", "host_key": key(99), "server_port": 22,
            "relay_port": 24001, "peers": [
                {"machine": "receiver", "relay_port": 24001},
                {"machine": "controller", "relay_port": 24002},
            ],
        }
        self.peers = [peer("controller", 2)]
        self.enabled = True
        self.calls: list[tuple[tuple[str, ...], bytes | None]] = []
        self.lose_reply = False

    def __call__(self, command: tuple[str, ...], payload: bytes | None) -> bytes:
        self.calls.append((command, payload))
        value: Any
        if command == ("export-client", "--machine", "receiver"):
            assert payload is None
            value = self.descriptor
        elif command == ("export-directory",):
            assert payload is None
            value = {"peers": self.peers}
        elif command in (("status",), ("import-peers",)):
            if command == ("import-peers",):
                assert payload is not None
                requested = json.loads(payload)
                assert set(requested) == {"peers"} and len(requested["peers"]) == 1
                for row in requested["peers"]:
                    prior = next((p for p in self.peers if p["machine"] == row["machine"]), None)
                    if prior is not None:
                        assert prior == row
                    else:
                        self.peers.append(row)
                if self.lose_reply:
                    raise TimeoutError("fixture lost the reply after native commit")
            else:
                assert payload is None
            value = {
                "installed": True, "enabled": self.enabled,
                "enrollment_enabled": bool(self.enabled and self.peers),
                "provider_owned": True, "authenticated": "unknown",
            }
        else:
            raise AssertionError(f"Undocumented native command: {command}")
        return json.dumps(value).encode()


def test_native_enrollment_and_same_identity_resume_use_only_existing_commands() -> None:
    provider = Provider()
    gateway = NativeGateway(provider, provider.descriptor)
    observed = gateway.enroll(peer())
    assert observed.admission_submitted
    assert observed.directory == {"peers": [peer("controller", 2), peer()]}
    assert "revision" not in observed.directory
    assert provider.calls[3] == (("import-peers",), json.dumps(
        {"peers": [peer()]}, sort_keys=True, ensure_ascii=True,
    ).encode() + b"\n")
    retained = deepcopy(provider.peers)
    resumed = gateway.enroll(peer(), existing_only=True)
    assert not resumed.admission_submitted and provider.peers == retained
    assert sum(command == ("import-peers",) for command, _ in provider.calls) == 1


def test_empty_enabled_native_gateway_can_admit_its_first_peer() -> None:
    provider = Provider()
    provider.peers = []
    assert NativeGateway(provider, provider.descriptor).enroll(peer()).admission_submitted


def test_lost_reply_is_uncertain_then_read_only_resume_confirms_same_native_identity() -> None:
    provider = Provider()
    gateway = NativeGateway(provider, provider.descriptor)
    provider.lose_reply = True
    with pytest.raises(AdmissionUncertain) as caught:
        gateway.enroll(peer())
    assert isinstance(caught.value.__cause__, TimeoutError)
    assert peer() in provider.peers
    resumed = gateway.enroll(peer(), existing_only=True)
    assert peer() in resumed.directory["peers"] and not resumed.admission_submitted
    assert sum(command == ("import-peers",) for command, _ in provider.calls) == 1


def test_missing_existing_identity_never_submits_an_admission() -> None:
    provider = Provider()
    with pytest.raises(PermissionError, match="not confirmed"):
        NativeGateway(provider, provider.descriptor).enroll(peer(), existing_only=True)
    assert all(command != ("import-peers",) for command, _ in provider.calls)


@pytest.mark.parametrize("change", ["target-key", "host-key", "user", "disabled"])
def test_conflicting_or_disabled_membership_refuses_before_mutation(change: str) -> None:
    provider = Provider()
    provider.peers.append(peer())
    if change == "target-key":
        provider.peers[-1]["public_key"] = key(8)
    elif change == "host-key":
        provider.peers[-1]["host_key"] = key(18)
    elif change == "user":
        provider.peers[-1]["user"] = "different"
    else:
        provider.enabled = False
    before = deepcopy(provider.peers)
    with pytest.raises(PermissionError):
        NativeGateway(provider, provider.descriptor).enroll(peer())
    assert provider.peers == before
    assert all(command != ("import-peers",) for command, _ in provider.calls)


@pytest.mark.parametrize("change", ["host-pin", "gateway-user", "target-port", "other-port", "removed"])
def test_descriptor_drift_refuses_before_directory_or_admission(change: str) -> None:
    provider = Provider()
    gateway = NativeGateway(provider, provider.descriptor)
    if change == "host-pin":
        provider.descriptor["host_key"] = key(98)
    elif change == "gateway-user":
        provider.descriptor["gateway_user"] = "different"
    elif change == "target-port":
        provider.descriptor["relay_port"] = 24003
        provider.descriptor["peers"][0]["relay_port"] = 24003
    elif change == "other-port":
        provider.descriptor["peers"][1]["relay_port"] = 24003
    else:
        provider.descriptor["peers"].pop()
    with pytest.raises(PermissionError, match="selected bindings"):
        gateway.inspect()
    assert len(provider.calls) == 1


def test_additive_native_topology_is_usable_without_replacing_earlier_bindings() -> None:
    provider = Provider()
    gateway = NativeGateway(provider, provider.descriptor)
    provider.descriptor["peers"].append({"machine": "later-controller", "relay_port": 24003})
    provider.peers.append(peer("later-controller", 3))
    result = gateway.inspect()
    assert result.directory["peers"] == [peer("controller", 2), peer("later-controller", 3)]
    result.directory["peers"].clear()
    assert len(result.directory["peers"]) == 2


@pytest.mark.parametrize("change", ["unknown", "duplicate-name", "shared-key", "cross-role", "same-key"])
def test_entire_directory_is_validated_before_admission(change: str) -> None:
    provider = Provider()
    if change == "unknown":
        provider.peers.append(peer("undeclared", 3))
    elif change == "duplicate-name":
        provider.peers.append(peer("controller", 3))
    elif change == "shared-key":
        provider.peers.append({**peer(), "public_key": key(2)})
    elif change == "cross-role":
        provider.peers.append({**peer(), "public_key": key(12)})
    else:
        provider.peers[0]["host_key"] = provider.peers[0]["public_key"]
    with pytest.raises(ValueError):
        NativeGateway(provider, provider.descriptor).enroll(peer())
    assert all(command != ("import-peers",) for command, _ in provider.calls)


@pytest.mark.parametrize("output", [
    b'{"peers":[],"peers":[]}', b'{"peers":[],"revision":1}',
    b'{"peers":NaN}', b"not-json", b"[]", b"x" * (MAX_BYTES + 1),
])
def test_malformed_directory_never_becomes_an_authoritative_empty_result(output: bytes) -> None:
    provider = Provider()

    def call(command: tuple[str, ...], payload: bytes | None) -> bytes:
        return output if command == ("export-directory",) else provider(command, payload)

    with pytest.raises(ValueError):
        NativeGateway(call, provider.descriptor).enroll(peer())
    assert all(command != ("import-peers",) for command, _ in provider.calls)


def test_unconfirmed_postwrite_directory_remains_uncertain_without_replay() -> None:
    provider = Provider()
    reads = 0

    def call(command: tuple[str, ...], payload: bytes | None) -> bytes:
        nonlocal reads
        result = provider(command, payload)
        if command == ("export-directory",):
            reads += 1
            if reads == 2:
                return b'{"peers":[]}'
        return result

    with pytest.raises(AdmissionUncertain):
        NativeGateway(call, provider.descriptor).enroll(peer())
    assert sum(command == ("import-peers",) for command, _ in provider.calls) == 1


def test_real_legacy_command_parser_and_public_stdin_round_trip(tmp_path: Path) -> None:
    provider = Provider()
    state = tmp_path / "native public state.json"
    state.write_text(json.dumps({
        "descriptor": provider.descriptor, "peers": provider.peers, "enabled": True,
    }), encoding="ascii")
    fixture = Path(__file__).parent / "fixtures" / "native_gateway_provider.py"
    commands = []

    def call(command: tuple[str, ...], payload: bytes | None) -> bytes:
        commands.append(command)
        result: subprocess.CompletedProcess[bytes] = subprocess.run(
            [sys.executable, str(fixture), "--state", str(state), *command],
            input=payload, capture_output=True, timeout=10, check=True, **no_window_kwargs(),
        )
        return result.stdout

    gateway = NativeGateway(call, provider.descriptor)
    assert gateway.enroll(peer()).admission_submitted
    assert not gateway.enroll(peer(), existing_only=True).admission_submitted
    assert json.loads(state.read_text(encoding="ascii"))["peers"] == [peer("controller", 2), peer()]
    assert commands.count(("import-peers",)) == 1


@pytest.mark.parametrize("failure", ["interrupted", "prior-member-lost"])
def test_postsubmission_interrupt_or_membership_loss_requires_reconciliation(failure: str) -> None:
    provider = Provider()

    def call(command: tuple[str, ...], payload: bytes | None) -> bytes:
        result = provider(command, payload)
        if command == ("import-peers",):
            if failure == "interrupted":
                raise KeyboardInterrupt()
            provider.peers.remove(peer("controller", 2))
        return result

    with pytest.raises(AdmissionUncertain) as caught:
        NativeGateway(call, provider.descriptor).enroll(peer())
    if failure == "interrupted":
        assert isinstance(caught.value.__cause__, KeyboardInterrupt)
    else:
        assert "retain" in str(caught.value.__cause__)
    assert sum(command == ("import-peers",) for command, _ in provider.calls) == 1
