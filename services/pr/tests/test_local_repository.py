"""Actual Unix client/OS admission without Core, Gateway or provider credentials."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import time
import uuid
from typing import Any

import pytest

from agent_pull_requests.watch_service import WatchService
from agent_pull_requests_service.__main__ import main
from agent_pull_requests_service.local_client import LocalClient, _Connection
from agent_pull_requests_service.local_config import LocalHostConfig, load_service_config

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux OS peer credential ingress")

NATIVE = '''import json,os,sys
from pathlib import Path
identity=json.loads(sys.argv[1]);state=Path(sys.argv[2]);log=Path(sys.argv[3]);args=sys.argv[4:]
kind="contract" if "--contract" in args else args[1] if len(args)>1 else "unknown"
allowed=args[:2] in (["pr-watch","inventory"],["pr-watch","snapshot"])
record={"kind":kind,"forge_writes":int(not allowed),"task_emissions":int("dispatch" in args)}
fd=os.open(log,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
os.write(fd,(json.dumps(record)+"\\n").encode());os.close(fd)
if not allowed: raise SystemExit(2)
if kind=="contract":
 print(json.dumps({"protocol":"agent-worktrees.pr-inventory/v1","providers":["github","gitea"],"credential":"observe/per-request","effects":"none","snapshot_request_budget":64,"snapshot_capture_bytes":1048584}))
else:
 data=json.loads(state.read_text())
 if kind=="inventory":
  print(json.dumps({"schema":"agent-worktrees.pr-inventory","version":1,"provider":identity["provider"],"api_base":identity["endpoint"],"repo":identity["repo"],"outcome":data.get("outcome","complete"),"numbers":data.get("members",[7]),"pages":2,"error":"","max_pages":4,"max_pulls":200}))
 else:
  print(json.dumps({"schema":"agent-worktrees.pr-snapshot","version":1,"provider":identity["provider"],"api_base":identity["endpoint"],"repo":identity["repo"],"pr":int(args[3]),"pr_state":data.get("state","open"),"merged":data.get("merged",False),"head_sha":data["head"],"base_ref":"dev","mergeable":None,"checks_state":"","title":"Synthetic","draft":False,"reviews":[]}))
'''


def configuration(tmp_path: Path) -> tuple[Path, dict[str, Any], Path, Path]:
    root = tmp_path.parent / ("m1-" + uuid.uuid4().hex[:6])
    script, state, log = tmp_path / "native.py", tmp_path / "provider.json", tmp_path / "calls.jsonl"
    script.write_text(NATIVE)
    state.write_text(json.dumps({"head": "a"}))
    identities = [{
        "namespace": "test", "provider": provider, "endpoint": endpoint,
        "account": "reader", "repo": "owner/repo",
    } for provider, endpoint in (("github", "https://github.com"), ("gitea", "https://forge.example"))]
    uid = int(getattr(os, "getuid", lambda: -1)())
    value: dict[str, Any] = {
        "schema": "agent-pull-requests.service", "version": 2, "profile": "repository-local/v1",
        "root": str(root), "namespace": "test", "interval": 1,
        "operation_seconds": 10, "drain_timeout": 10,
        "repository_cache": {
            "protocol": "agent-pull-requests.repository-cache/v1", "socket": str(root / "local.sock"),
            "peers": [{"uid": uid, "principal": "reader-a"},
                      {"uid": uid + 1, "principal": "reader-b"}],
            "repositories": [{
                "target": "repo-" + identity["provider"], "identity": identity,
                "prefix": [sys.executable, str(script), json.dumps(identity), str(state), str(log)],
                "credential_command": [sys.executable], "source_config": str(tmp_path / "native.yaml"),
            } for identity in identities],
            "bindings": [
                {"principal": "reader-a", "target": "repo-github", "consumer": "consumer-a"},
                {"principal": "reader-b", "target": "repo-gitea", "consumer": "consumer-b"},
            ],
        },
    }
    path = tmp_path / "host.json"
    path.write_text(json.dumps(value))
    return path, value, state, log


def test_local_config_is_passive_strict_fresh_and_has_no_gateway_credentials(tmp_path: Path) -> None:
    path, value, _state, log = configuration(tmp_path)
    config = load_service_config(path)
    assert isinstance(config, LocalHostConfig)
    assert not config.root.exists() and not log.exists()
    assert main(["provision", "--config", str(path)]) == 0
    before = config.store().path.read_bytes()
    assert main(["provision", "--config", str(path)]) == 1
    assert config.store().path.read_bytes() == before and not log.exists()
    value["proof_key_file"] = "forbidden"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        load_service_config(path)


def test_real_local_client_continues_after_ack_with_scope_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    path, _value, state, log = configuration(tmp_path)
    config = load_service_config(path)
    assert isinstance(config, LocalHostConfig)
    config.provision()
    from agent_pull_requests import watch_service
    monkeypatch.setattr(watch_service, "ServiceConnector",
                        lambda **_kwargs: pytest.fail("local profile constructed Gateway connector"))

    async def scenario() -> None:
        service = WatchService(
            config.store(), fleet=None, gateway=None, slot=None,
            bindings=config.bindings, observations=config.observations,
            gateway_origin=None, access_token_callback=None, proof_key=None,
            authority=lambda: int(time.time()) + 10, checkpoint=lambda: None,
            interval=.1, local_socket=config.repositories.socket_path,
            local_peers=config.repositories.peers, operation_seconds=10,
        )
        await service.start()
        try:
            await asyncio.wait_for(service.connector.connected.wait(), 5)
            client = LocalClient(config.repositories.socket_path, "test", "repo-github")
            async def request(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
                for _ in range(50):
                    try:
                        return await asyncio.to_thread(client.request, operation, payload)
                    except ValueError:
                        await asyncio.sleep(.02)
                raise AssertionError("bounded local request did not settle")
            for _ in range(100):
                result = await request("query", {})
                if result["pulls"] and result["pulls"][0]["facts"] is not None:
                    break
                await asyncio.sleep(.02)
            assert result["pulls"][0]["facts"]["head_sha"] == "a"
            for _ in range(50):
                capsys.readouterr()
                code = await asyncio.to_thread(main, [
                    "query", "--socket", str(config.repositories.socket_path),
                    "--namespace", "test", "--target", "repo-github",
                ])
                output = capsys.readouterr()
                if code == 0:
                    break
                await asyncio.sleep(.02)
            assert code == 0 and json.loads(output.out)["pulls"][0]["facts"]["head_sha"] == "a"
            with config.store()._lock():
                waiting = asyncio.create_task(asyncio.to_thread(client.request, "query", {}))
                await asyncio.sleep(.1)
                assert not waiting.done()
            assert (await asyncio.wait_for(waiting, 5))["pulls"][0]["facts"]["head_sha"] == "a"
            first = (await request("subscribe", {"request_id": "first", "ttl": 3600}))["subscription"]
            second = (await request("subscribe", {"request_id": "second", "ttl": 3600}))["subscription"]
            assert first["registration_id"] != second["registration_id"]
            state.write_text(json.dumps({"head": "b"}))
            for _ in range(100):
                pending = await request("events", {"registration_id": first["registration_id"]})
                if pending["events"]:
                    break
                await asyncio.sleep(.02)
            assert pending["events"][-1]["kind"] == "change"
            event = pending["events"][-1]
            await request("acknowledge", {
                "registration_id": first["registration_id"], "event_id": event["event_id"],
            })
            assert not (await request("events", {"registration_id": first["registration_id"]}))["events"]
            other = await request("events", {"registration_id": second["registration_id"]})
            assert other["events"][-1]["event_id"] == event["event_id"]
            def spoof() -> int:
                connection = _Connection(config.repositories.socket_path)
                try:
                    connection.request("GET", "/v1/repositories/repo-gitea/capabilities",
                                       headers={"x-principal": "reader-b"})
                    return connection.getresponse().status
                finally:
                    connection.close()
            assert await asyncio.to_thread(spoof) == 403
            records = [json.loads(line) for line in log.read_text().splitlines()]
            assert sum(record["forge_writes"] for record in records) == 0
            assert sum(record["task_emissions"] for record in records) == 0
        finally:
            await service.close(timeout=10)
        assert not service.status()["writer_held"] and not config.repositories.socket_path.exists()
        restarted = WatchService(
            config.store(), fleet=None, gateway=None, slot=None,
            bindings=config.bindings, observations=config.observations,
            gateway_origin=None, access_token_callback=None, proof_key=None,
            authority=lambda: int(time.time()) + 10, checkpoint=lambda: None,
            interval=.1, local_socket=config.repositories.socket_path,
            local_peers=config.repositories.peers, operation_seconds=10,
        )
        await restarted.start()
        try:
            await asyncio.wait_for(restarted.connector.connected.wait(), 5)
            client = LocalClient(config.repositories.socket_path, "test", "repo-github")
            recovered = await request("events", {"registration_id": second["registration_id"]})
            assert recovered["events"][-1]["event_id"] == event["event_id"]
            assert not (await request("events", {"registration_id": first["registration_id"]}))["events"]
            await request("cancel", {"registration_id": second["registration_id"]})
            assert (await request("inspect", {
                "registration_id": second["registration_id"],
            }))["status"] == "cancelled"
        finally:
            await restarted.close(timeout=10)
    asyncio.run(scenario())
