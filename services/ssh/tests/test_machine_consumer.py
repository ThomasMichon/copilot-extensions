"""Installed Fleet role, real authenticated Gateway and both native local writers."""

import asyncio
from contextlib import AsyncExitStack
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import sysconfig
from types import SimpleNamespace

from cryptography.hazmat.primitives import serialization
from joserfc.jwk import ECKey
import pytest

from agent_ssh_service.config import ServiceConfig
from agent_ssh_service.hosting import run
from fleet_manager.gateway_service_caller import ServiceCaller
from fleet_manager.ssh_directory import SSHDirectoryClient
from fleet_manager.ssh_machine import run as run_machine
from fleet_manager.ssh_machine_config import SSHMachineConfig

from conftest import document, enrollment, key
from test_gateway_integration import Rig, eventually


def test_consumer_packages_are_installed_not_loaded_from_source():
    runtime = Path(sysconfig.get_path("purelib")).resolve()
    for name in ("fleet_manager", "agent_ssh"):
        selected = importlib.util.find_spec(name)
        assert selected is not None and selected.origin is not None
        assert Path(selected.origin).resolve().is_relative_to(runtime)


def published_text(path):
    try:
        return path.read_text("utf-8")
    except (FileNotFoundError, PermissionError):
        # A Windows atomic replacement can briefly refuse the observer's open.
        return None


def proof_file(path, key):
    path.write_bytes(key.get_op_key("sign").private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    return str(path)


@pytest.mark.parametrize("destination", [
    "config_d", "ssh_config", "authorization_dir", "source-state", "lock-state",
])
def test_installed_cli_rejects_lease_tree_outputs_before_credentials(tmp_path, destination):
    value = {
        "schema": "fleet-manager.ssh-client", "version": 1,
        "gateway_origin": "https://gateway.example.test", "gateway_ca": None,
        "namespace": "/ssh", "machine": "worker-a", "environment": "linux",
        "gateway": "selected-gateway", "controller": "gateway-a",
        **{name: str(tmp_path / name) for name in (
            "state_dir", "proof_key_file", "access_token_file", "policy_path",
            "identity_file", "public_key_file", "authorization_dir", "config_d", "ssh_config",
        )},
    }
    if destination in ("source-state", "lock-state"):
        from agent_ssh import gateway_profile, ssh_profile

        directory = gateway_profile._SOURCE_DIR if destination == "source-state" else ssh_profile._LOCK_DIR_NAME
        value["state_dir"] = str(Path(value["config_d"]).parent / directory)
    else:
        value[destination] = str(Path(value["state_dir"]) / "nested" / "output")
    selected = tmp_path / "client.json"
    selected.write_text(json.dumps(value), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-I", "-m", "fleet_manager", "serve",
         "--role", "ssh-client", "--config", str(selected)],
        cwd=tmp_path, capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 1
    assert "requires a dedicated state directory" in result.stderr
    assert not Path(value["state_dir"]).exists()
    assert not Path(value["proof_key_file"]).exists()


async def settle_resources(*, rig, diagnostic, service_owner, process_stop, second_owner):
    async with AsyncExitStack() as cleanup:
        cleanup.push_async_callback(rig.close)
        if diagnostic is not None:
            cleanup.callback(diagnostic.close)
        if service_owner is not None:
            cleanup.push_async_callback(asyncio.wait_for, service_owner, 10)
        if process_stop is not None:
            cleanup.push_async_callback(process_stop)
        if second_owner is not None:
            cleanup.push_async_callback(asyncio.wait_for, second_owner, 10)


async def test_failed_consumer_still_settles_every_other_retained_resource():
    settled = []

    async def finish(name, *, fail=False):
        settled.append(name)
        if fail:
            raise PermissionError("fixture-second-consumer-refused")

    service = asyncio.create_task(finish("service"))
    second = asyncio.create_task(finish("second", fail=True))
    with pytest.raises(PermissionError, match="fixture-second-consumer-refused"):
        await settle_resources(
            rig=SimpleNamespace(close=lambda: finish("rig")),
            diagnostic=SimpleNamespace(close=lambda: settled.append("diagnostic")),
            service_owner=service, process_stop=lambda: finish("process"), second_owner=second,
        )
    assert set(settled) == {"service", "second", "rig", "diagnostic", "process"}
    assert len(settled) == 5
    assert service.done() and second.done()


def machine_config(root, service, rig, index):
    root.mkdir()
    name = "worker-a" if index == 0 else "worker-b"
    (root / "policy.json").write_text(json.dumps(service["policy"]), encoding="utf-8")
    (root / "identity").write_text("fixture key reference; no login attempted", encoding="ascii")
    (root / "identity.pub").write_text(key(index + 1), encoding="ascii")
    (root / "access").write_text(rig.access("caller-a" if index == 0 else "caller-b", index + 3))
    value = {
        "schema": "fleet-manager.ssh-client", "version": 1,
        "state_dir": str(root / "state"), "gateway_origin": rig.origin,
        "gateway_ca": rig.ca, "namespace": "/api",
        "proof_key_file": proof_file(root / "proof.pem", rig.h.keys[index + 3]),
        "access_token_file": str(root / "access"), "policy_path": str(root / "policy.json"),
        "machine": name, "environment": "linux", "gateway": "selected-gateway",
        "controller": "gateway-a", "identity_file": str(root / "identity"),
        "public_key_file": str(root / "identity.pub"),
        "authorization_dir": str(root / "authorization"),
        "config_d": str(root / "ssh" / "config.d"), "ssh_config": str(root / "ssh" / "config"),
    }
    selected = root / "client.json"
    selected.write_text(json.dumps(value), encoding="utf-8")
    return selected, value


@pytest.mark.parametrize("disconnected", [False, True])
async def test_installed_fleet_role_converges_live_and_catches_up_after_restart(
    tmp_path, disconnected,
):
    rig = Rig(tmp_path)
    rig.h.keys += (ECKey.generate_key("P-256"),)
    rig.h.document["manifest"]["key_sources"].append({
        "name": "caller-b", "provider": "owner-store", "reference": "keys/caller-b",
    })
    rig.h.document["bindings"].append({
        "provider": "owner-store", "reference": "keys/caller-b",
        "jwk": rig.h.keys[4].as_dict(private=False),
    })
    rig.h.document["callers"].append({
        "principal": "caller-b", "key_source": "caller-b",
        "slots": ["service-a"], "methods": ["GET", "POST"],
    })
    stop, machine_stop = asyncio.Event(), asyncio.Event()
    service_owner = second_owner = process = diagnostic = None

    def launch(selected):
        return subprocess.Popen(
            [sys.executable, "-I", "-m", "fleet_manager", "serve",
             "--role", "ssh-client", "--config", str(selected)],
            cwd=tmp_path, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=diagnostic,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

    async def stop_process():
        if process.poll() is None:
            if os.name == "nt":
                process.terminate()  # Windows termination deliberately tests kernel-lease recovery.
            else:
                process.send_signal(signal.SIGTERM)
        code = await asyncio.to_thread(process.wait, 5)
        if os.name != "nt":
            assert code == 0

    try:
        await rig.start()
        service = document(tmp_path / "service-state")
        service.update(slot="service-a", gateway_origin=rig.origin, gateway_ca=rig.ca)
        for index, row in enumerate(service["bindings"]):
            row["principal"] = "caller-a" if index == 0 else "caller-b"
        service["proof_key_file"] = proof_file(tmp_path / "service-proof.pem", rig.h.keys[1])
        service["access_token_file"] = str(tmp_path / "service-access")
        Path(service["access_token_file"]).write_text(rig.access("service-a", 1))
        service_owner = asyncio.create_task(run(ServiceConfig.from_dict(service), stop=stop))
        await eventually(lambda: "service-a" in rig.state.channels, timeout=30)

        def client(index):
            async def access():
                return rig.access("caller-a" if index == 0 else "caller-b", index + 3)
            return SSHDirectoryClient(ServiceCaller(
                gateway_origin=rig.origin, gateway_ca=rig.ca,
                access_token_callback=access, proof_key=rig.h.keys[index + 3].get_op_key("sign"),
            ), namespace="/api")

        first, second = client(0), client(1)
        await first.enroll(enrollment())
        selected, _ = machine_config(tmp_path / "machine-a", service, rig, 0)
        diagnostic = (tmp_path / "client.stderr").open("w+b")
        process = launch(selected)
        authorized = tmp_path / "machine-a" / "authorization" / "authorized_keys.gateway"

        def converged(peer_key):
            if process.poll() is not None:
                diagnostic.seek(0)
                pytest.fail("Installed Fleet SSH role exited: "
                            + diagnostic.read(4096).decode("utf-8", errors="replace"))
            content = published_text(authorized)
            return content is not None and (peer_key is None or peer_key in content)

        await eventually(lambda: converged(None), timeout=30)
        assert key(2) not in authorized.read_text("utf-8")
        if disconnected:
            await stop_process()
            process = None
        joined = await second.enroll(enrollment(1))
        assert joined.revision == 3
        if disconnected:
            process = launch(selected)
        await eventually(lambda: converged(key(2)), timeout=30)
        assert any("Host worker-b" in path.read_text("utf-8")
                   for path in (tmp_path / "machine-a" / "ssh" / "config.d").glob("*.conf"))
        _, second_config = machine_config(tmp_path / "machine-b", service, rig, 1)
        second_owner = asyncio.create_task(run_machine(
            SSHMachineConfig.from_dict(second_config), stop=machine_stop,
        ))
        second_keys = tmp_path / "machine-b" / "authorization" / "authorized_keys.gateway"
        def second_converged():
            if second_owner.done():
                second_owner.result()
                pytest.fail("Second Fleet consumer stopped before convergence")
            content = published_text(second_keys)
            return content is not None and key(1) in content

        await eventually(second_converged, timeout=30)
        before = authorized.read_bytes(), authorized.stat().st_mtime_ns
        assert (await second.enroll(enrollment(1))).revision == 3
        assert (authorized.read_bytes(), authorized.stat().st_mtime_ns) == before
        assert rig.backends[0].requests == []
    finally:
        machine_stop.set()
        stop.set()
        await settle_resources(
            rig=rig, diagnostic=diagnostic, service_owner=service_owner,
            process_stop=stop_process if process is not None else None, second_owner=second_owner,
        )
