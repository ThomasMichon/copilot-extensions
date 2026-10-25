"""Actual TLS/JWT/DPoP/registered channel; no production issuer or host access."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

from cryptography.hazmat.primitives import serialization
from joserfc.jwk import ECKey
import pytest

from agent_ssh_service.config import ServiceConfig
from agent_ssh_service.hosting import run
from fleet_manager.gateway_service_caller import ServiceCaller
from fleet_manager.gateway_service_channel import TransportError
from fleet_manager.ssh_directory import SSHDirectoryClient

from conftest import document, enrollment

# Reuse the canonical synthetic issuer/CA and real Gateway, not a second protocol fixture.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "fleet-manager" / "tests"))
from gateway_service_transport_fixtures import Rig, eventually


async def milestone(event, owner):
    waiting = asyncio.create_task(event.wait())
    try:
        done, _ = await asyncio.wait({waiting, owner}, timeout=5,
                                     return_when=asyncio.FIRST_COMPLETED)
        if owner in done:
            await owner
        assert waiting in done, "Registered SSH consumer did not reach its milestone"
    finally:
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)


@pytest.mark.parametrize("external_process", [False, True])
async def test_registered_service_delivers_later_join_to_authenticated_connected_machine(
    tmp_path, external_process,
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
    stop, watch_stop = asyncio.Event(), asyncio.Event()
    owner = watch = process = diagnostic = None
    try:
        await rig.start()
        value = document(tmp_path / "ssh-state")
        value.update(slot="service-a", gateway_origin=rig.origin, gateway_ca=rig.ca)
        for index, row in enumerate(value["bindings"]):
            row["principal"] = "caller-a" if index == 0 else "caller-b"
        value["proof_key_file"] = str(tmp_path / "service-proof.pem")
        value["access_token_file"] = str(tmp_path / "service-access")
        Path(value["proof_key_file"]).write_bytes(
            rig.h.keys[1].get_op_key("sign").private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ))
        Path(value["access_token_file"]).write_text(rig.access("service-a", 1), encoding="utf-8")
        config = ServiceConfig.from_dict(value)
        if external_process:
            selected = tmp_path / "service.json"
            selected.write_text(json.dumps(value), encoding="utf-8")
            diagnostic = (tmp_path / "service.stderr").open("w+b")
            process = subprocess.Popen(
                [sys.executable, "-I", "-m", "agent_ssh_service", "--config", str(selected)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=diagnostic,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                cwd=tmp_path,
            )
        else:
            owner = asyncio.create_task(run(config, stop=stop))
        def registered():
            if process is not None and process.poll() is not None:
                diagnostic.seek(0)
                pytest.fail("Installed SSH service exited: "
                            + diagnostic.read(2048).decode("utf-8", errors="replace"))
            if owner is not None and owner.done():
                owner.result()
            return "service-a" in rig.state.channels

        try:
            await eventually(registered, timeout=30)
        except asyncio.TimeoutError:
            if diagnostic is not None:
                diagnostic.seek(0)
                pytest.fail("Installed SSH host did not register: "
                            + diagnostic.read(4096).decode("utf-8", errors="replace"))
            raise
        assert rig.state.channels["service-a"].caller_context

        def client(principal, key):
            async def access():
                return rig.access(principal, key)

            return SSHDirectoryClient(ServiceCaller(
                gateway_origin=rig.origin, access_token_callback=access,
                proof_key=rig.h.keys[key].get_op_key("sign"), gateway_ca=rig.ca,
            ), namespace="/api")

        first, second = client("caller-a", 3), client("caller-b", 4)
        joined = await first.enroll(enrollment())
        assert joined.revision == 2
        initial, updated = asyncio.Event(), asyncio.Event()
        received = []

        def accept(update):
            received.append(update.to_dict())
            initial.set()
            if update.revision == 3:
                updated.set()

        rig.state.stopping = True
        watch = asyncio.create_task(first.watch(watch_stop, accept))
        await asyncio.sleep(0.1)
        assert not watch.done(), "A real challenge-stage 503 permanently retired the watch"
        rig.state.stopping = False
        await milestone(initial, watch)
        with pytest.raises(TransportError, match="ssh-enrollment-refused"):
            await second.enroll(enrollment())
        later = await second.enroll(enrollment(1))
        await milestone(updated, watch)
        assert received[-1] == later.to_dict()
        assert len(received[-1]["directory"]["machines"]) == 2
        assert (await first.wait(0, seconds=0)).encoded == later.encoded
        assert (await second.enroll(enrollment(1))).encoded == later.encoded
        async with rig.session.get(rig.origin + "/api/directory") as anonymous:
            assert anonymous.status == 401
        rig.h.document["callers"] = []
        await rig.update()
        with pytest.raises(TransportError):
            await first.wait(later.revision, seconds=0)
        assert rig.backends[0].requests == []
    finally:
        watch_stop.set()
        stop.set()
        if watch is not None:
            await asyncio.gather(watch, return_exceptions=True)
        if owner is not None:
            await asyncio.wait_for(owner, 10)
        if process is not None:
            if process.poll() is None:
                process.terminate()
            await asyncio.to_thread(process.wait, 5)
        if diagnostic is not None:
            diagnostic.close()
        await rig.close()
