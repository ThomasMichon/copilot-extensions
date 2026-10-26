"""Real registered Gateway transport with only disposable issuer/key material."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

from cryptography.hazmat.primitives import serialization
from joserfc.jwk import ECKey
import pytest

from fleet_manager.gateway_service_caller import ServiceCaller
from fleet_manager.gateway_service_channel import TransportError
from fleet_manager.ssh_approval import PeerEnrollmentClient
from fleet_manager.ssh_directory import SSHDirectoryClient

from conftest import RealtimeClock, document, enrollment
from test_approval import decision
from test_gateway_integration import Rig, eventually


async def test_registered_peer_approval_recovery_and_restart(tmp_path):
    rig = Rig(tmp_path)
    # Restarted native processes use real time, not the unit rig's frozen proof clock.
    rig.h.clock = RealtimeClock()
    rig.h.keys += (ECKey.generate_key("P-256"), ECKey.generate_key("P-256"))
    for index, principal in ((4, "caller-b"), (5, "caller-recovery")):
        reference = "keys/" + principal
        rig.h.document["manifest"]["key_sources"].append({
            "name": principal, "provider": "owner-store", "reference": reference,
        })
        rig.h.document["bindings"].append({
            "provider": "owner-store", "reference": reference,
            "jwk": rig.h.keys[index].as_dict(private=False),
        })
        rig.h.document["callers"].append({
            "principal": principal, "key_source": principal,
            "slots": ["service-a"], "methods": ["GET", "POST"],
        })
    process, waiting = None, None
    diagnostic = None
    try:
        await rig.start()
        value = document(tmp_path / "ssh-state")
        value.update(slot="service-a", gateway_origin=rig.origin, gateway_ca=rig.ca)
        for index, row in enumerate(value["bindings"]):
            row["principal"] = "caller-a" if index == 0 else "caller-b"
        value["enrollment_policy"] = {
            "mode": "peer-approval", "recovery_principals": ["caller-recovery"],
        }
        value["proof_key_file"] = str(tmp_path / "service-proof.pem")
        value["access_token_file"] = str(tmp_path / "service-access")
        Path(value["proof_key_file"]).write_bytes(
            rig.h.keys[1].get_op_key("sign").private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ))
        Path(value["access_token_file"]).write_text(rig.access("service-a", 1), encoding="utf-8")
        selected = tmp_path / "service.json"
        selected.write_text(json.dumps(value), encoding="utf-8")
        diagnostic = (tmp_path / "service.stderr").open("w+b")

        async def start_owner():
            nonlocal process
            process = subprocess.Popen(
                [sys.executable, "-I", "-m", "agent_ssh_service", "--config", str(selected)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=diagnostic,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                cwd=tmp_path,
            )

            def registered():
                if process is not None and process.poll() is not None:
                    diagnostic.seek(0)
                    pytest.fail("Disposable SSH owner exited: "
                                + diagnostic.read(2048).decode("utf-8", errors="replace"))
                return "service-a" in rig.state.channels

            await eventually(registered, timeout=30)

        async def stop_owner():
            nonlocal process
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                await asyncio.to_thread(process.wait, 5)
                process = None

        await start_owner()

        def caller(principal, key):
            async def access():
                return rig.access(principal, key)

            return ServiceCaller(
                gateway_origin=rig.origin, access_token_callback=access,
                proof_key=rig.h.keys[key].get_op_key("sign"), gateway_ca=rig.ca,
            )

        first_caller, second_caller = caller("caller-a", 3), caller("caller-b", 4)
        first = PeerEnrollmentClient(first_caller, namespace="/api")
        second = PeerEnrollmentClient(second_caller, namespace="/api")
        recovery = PeerEnrollmentClient(caller("caller-recovery", 5), namespace="/api")
        pending = (await first.submit(enrollment())).to_dict()
        assert pending["status"] == "pending" and "directory" not in pending
        with pytest.raises(TransportError):
            await first.approve(**decision(pending))
        with pytest.raises(TransportError):
            await first.recover(**decision(pending), confirm=True, reason="first-peer")
        initial = await recovery.recover(
            **decision(pending), confirm=True, reason="first-peer")
        revision = initial.to_dict()["decision"]["directory_revision"]
        waiting = asyncio.create_task(SSHDirectoryClient(
            first_caller, namespace="/api").wait(revision, seconds=10))
        pending = (await second.submit(enrollment(1))).to_dict()
        await asyncio.sleep(0.05)
        assert not waiting.done()
        assert (await first.pending())["requests"][0]["fingerprint"] == pending["fingerprint"]
        page = await first.pending(after="caller-a")
        assert page["requests"][0]["fingerprint"] == pending["fingerprint"]
        assert (await first.pending(after="caller-b"))["requests"] == []
        with pytest.raises(TransportError):
            await first.approve(**decision(pending, fingerprint="SHA256:" + "A" * 43))
        active = await first.approve(**decision(pending))
        notice = await asyncio.wait_for(waiting, 5)
        assert notice.revision == revision + 1
        assert (await first.approve(**decision(pending))).encoded == active.encoded
        assert (await second.status(pending["request_id"])).encoded == active.encoded
        async with rig.session.get(rig.origin + "/api/enrollment/pending") as anonymous:
            assert anonymous.status == 401
        await stop_owner()
        await eventually(lambda: "service-a" not in rig.state.channels, timeout=10)
        await start_owner()
        restored = await second.status(pending["request_id"])
        assert restored.encoded == active.encoded
        assert (await first.pending())["requests"] == []
        assert rig.backends[0].requests == []
    finally:
        if waiting is not None:
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)
        if process is not None:
            if process.poll() is None:
                process.terminate()
            await asyncio.to_thread(process.wait, 5)
        if diagnostic is not None:
            diagnostic.close()
        await rig.close()
