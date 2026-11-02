import base64
import struct
import time

import pytest

from agent_ssh_service.config import ServiceConfig
from fleet_manager.private_state import private_directory


class RealtimeClock:
    @property
    def value(self):
        return int(time.time())

    def __call__(self):
        return self.value


def key(number):
    wire = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + bytes([number]) * 32
    return "ssh-ed25519 " + base64.b64encode(wire).decode()


def document(root):
    machines = [{
        "machine": name, "environment": "linux", "driver": "gateway",
        "role": "interactive", "inbound": True, "outbound": True,
        "controller": "gateway-a", "gateway_egress": True,
    } for name in ("worker-a", "worker-b")]
    return {
        "schema": "agent-ssh.service", "version": 1, "revision": 1,
        "data_dir": str(root), "fleet": "fleet-a", "gateway": "gateway-a",
        "slot": "ssh", "controller": "gateway-a",
        "gateway_origin": "https://gateway.example.test",
        "gateway_ca": None, "proof_key_file": str(root / "proof.pem"),
        "access_token_file": str(root / "access"),
        "policy": {
            "schema": "fleet-manager.gateway-ssh-policy", "version": 1,
            "revision": "r1", "controllers": ["gateway-a"], "machines": machines,
        },
        "bindings": [{
            "machine": row["machine"], "environment": "linux",
            "principal": "principal-" + row["machine"], "generation": 1,
            "alias": row["machine"], "user": "owner", "reverse_port": 24001 + index,
        } for index, row in enumerate(machines)],
    }


def enrollment(index=0):
    name = "worker-a" if index == 0 else "worker-b"
    return {
        "request_id": "join-" + name, "machine": name, "environment": "linux",
        "generation": 1, "public_key": key(index + 1), "host_key": key(index + 11),
    }


@pytest.fixture
def config(tmp_path):
    root = tmp_path / "state"
    private_directory(root)
    return ServiceConfig.from_dict(document(root))
