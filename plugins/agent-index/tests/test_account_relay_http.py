"""Real native HTTP/source/account/capability boundary, using synthetic providers."""

import asyncio
import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from zipfile import ZipFile
from email.parser import BytesParser

import httpx
import pytest
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "libs" / "credential-relay" / "src"))
from credential_relay.account_relay import AccountRelay
from credential_relay.sources.gh_auth import GhAuthSource

from agent_index.server import build_app, INSTALLATION_HEADER
from agent_index.source_account_relay import acquire


def test_native_source_http_capabilities_renewal_denial_and_disconnect(tmp_path, monkeypatch):
    async def scenario():
        calls = []

        async def provider(self, action, fields, *, timeout):
            calls.append(self._account)
            return (
                f"protocol=https\nhost=github.com\nusername={self._account}\n"
                f"token=synthetic-{self._account}-{len(calls)}\n\n"
            )

        monkeypatch.setattr(GhAuthSource, "resolve", provider)
        grants, bindings, providers, sources = [], [], {}, []
        for name in ("primary", "secondary"):
            capability = tmp_path / f"capability {name}"
            capability.write_text(f"synthetic-capability-{name}-01234567890123456789")
            binding = {
                "source": f"github:example/{name}", "account": f"account-{name}",
                "recipient": "index-worker", "host": "github.com", "action": "get-github-token",
                "capability_file": str(capability),
            }
            bindings.append(binding)
            grants.append(dict(binding, provider=name, expires_at=time.time() + 3600))
            providers[name] = {"kind": "gh-auth", "account": binding["account"], "host": "github.com"}
        source_policy = tmp_path / "source.json"
        source_policy.write_text(json.dumps({
            "schema": "credential-relay.account-policy", "version": 1,
            "providers": providers, "grants": grants,
        }))
        relay = AccountRelay(str(source_policy))
        await relay.start()
        worker = tmp_path / "worker.json"
        worker_policy = {
            "schema": "credential-relay.account-worker", "version": 1,
            "transport": {"host": "127.0.0.1", "port": relay._publication["port"]},
            "bindings": bindings,
        }
        worker.write_text(json.dumps(worker_policy))
        for binding in bindings:
            sources.append({
                "name": binding["source"], "type": "github",
                "auth": {"account": binding["account"], "relay": str(worker)},
            })
        monkeypatch.setenv("AGENT_INDEX_CONFIG_DATA_B64", base64.urlsafe_b64encode(
            json.dumps({"corpus": {"sources": sources}}).encode(),
        ).decode())
        monkeypatch.setenv("AGENT_INDEX_SOURCE_MODE", "explicit")
        monkeypatch.setenv("AGENT_INDEX_INSTALLATION_ID", "synthetic-index")
        monkeypatch.delenv("AGENT_INDEX_SOURCES", raising=False)
        app = build_app()
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        endpoint = f"http://127.0.0.1:{sock.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="critical"))
        serving = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            deadline = asyncio.get_running_loop().time() + 5
            while not server.started:
                assert asyncio.get_running_loop().time() < deadline
                await asyncio.sleep(0.01)
            async with httpx.AsyncClient(base_url=endpoint, timeout=5) as client:
                async def post(binding, capability, *, identity="synthetic-index"):
                    body = {k: v for k, v in binding.items() if k != "capability_file"}
                    body["capability"] = capability
                    return await client.post(
                        "/source-credentials", json=body, headers={INSTALLATION_HEADER: identity},
                    )

                first = bindings[0]
                secret = Path(first["capability_file"]).read_text()
                assert (await post(first, secret, identity="different-index")).status_code == 409
                assert (await post(first, "")).status_code == 403
                assert (await post(first, Path(bindings[1]["capability_file"]).read_text())).status_code == 503
                assert (await post(dict(first, source="github:example/unapproved"), secret)).status_code == 503
                assert not calls
                for binding in bindings:
                    response = await post(binding, Path(binding["capability_file"]).read_text())
                    assert response.status_code == 200
                    assert response.json()["account"] == binding["account"]
                    assert response.json()["token"].startswith("synthetic-" + binding["account"])
                assert calls == ["account-primary", "account-secondary"]
                Path(first["capability_file"]).write_text("rotated-synthetic-capability-01234567890123456789")
                assert (await post(first, secret)).status_code == 503
                # The HTTP façade must not borrow this newly stored native-worker capability.
                assert len(calls) == 2
                token = await asyncio.to_thread(
                    acquire, worker, source=first["source"], account=first["account"], host="github.com",
                )
                assert token.startswith("synthetic-account-primary")
                await relay.stop()
                response = await post(first, Path(first["capability_file"]).read_text())
                assert response.status_code == 503 and "token" not in response.json()
                await relay.start()
                worker_policy["transport"]["port"] = relay._publication["port"]
                worker.write_text(json.dumps(worker_policy))
                assert (await post(first, Path(first["capability_file"]).read_text())).status_code == 200
        finally:
            server.should_exit = True
            await asyncio.wait_for(serving, timeout=5)
            sock.close()
            await relay.stop()

    asyncio.run(scenario())


def test_native_core_wheel_contains_stdlib_consumer_without_broker_or_extra_family(tmp_path):
    wheel_value = os.environ.get("AGENT_INDEX_TEST_CORE_WHEEL")
    if not wheel_value:
        pytest.skip("explicit matching core-wheel artifact required for the isolated wheel lane")
    wheel = Path(wheel_value).resolve(strict=True)
    with ZipFile(wheel) as package:
        metadata_name = next(name for name in package.namelist() if name.endswith(".dist-info/METADATA"))
        metadata = BytesParser().parsebytes(package.read(metadata_name))
        assert "agent_index/sources/account_relay.py" not in package.namelist()
        assert "relay" not in metadata.get_all("Provides-Extra", [])
        assert all("extra" in value for value in metadata.get_all("Requires-Dist", [])
                   if value.lower().startswith("fleet-manager"))
        assert package.read("agent_index/source_account_relay.py").replace(
            b"\r\n", b"\n",
        ) == Path(sys.modules[acquire.__module__].__file__).read_bytes().replace(b"\r\n", b"\n")
    script = (
        "import sys; sys.path.insert(0,sys.argv[1]); "
        "from agent_index import source_account_relay as account_relay; "
        "assert '.whl' in account_relay.__file__; "
        "assert not any(n=='fleet_manager' or n.startswith('fleet_manager.') for n in sys.modules); "
        "print('native-core-consumer-ok')"
    )
    result = subprocess.run(
        [sys.executable, "-S", "-c", script, str(wheel)],
        capture_output=True, text=True, timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "native-core-consumer-ok"
