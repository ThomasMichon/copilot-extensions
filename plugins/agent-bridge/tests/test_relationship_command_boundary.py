"""Real retained-claim commands and callback processes with disposable authorities."""

from __future__ import annotations

import asyncio
import json
import socket
import sys
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, HTTPException

from agent_bridge import relationship_authority as authority, remote_relationships as relationships
from agent_bridge.acp_client import AcpClient
from agent_bridge.client import BridgeClient
from agent_bridge.models import ServiceConfig, StartSessionRequest
from agent_bridge.protocol import HTTP_PROTOCOL_VERSION
from agent_bridge.routes import sessions
from agent_bridge.session_manager import session_manager_from_config
from test_remote_acp_sessions import remote_manager as remote_manager, _remote_request
from test_remote_acp_peer import peer as peer

BOOTSTRAP = r"""
import contextlib, json, sys
from pathlib import Path
root, state, callback, base, *argv = sys.argv[1:]
root, state = Path(root), Path(state)
sys.path.insert(0, str(root / "plugins" / "agent-worktrees" / "src"))
for library in ("lazy-cli-dispatch", "work-coalescing-singleton", "update-telemetry"):
    sys.path.insert(0, str(root / "libs" / library / "src"))
from agent_worktrees import __main__ as main, config as cfg, tracking, claim_providers
project = state / "project"
records = project / "worktrees"
records.mkdir(parents=True, exist_ok=True)
config = cfg.Config(srcroot=str(state), machine="local", platform="linux", repo_name="example",
    repos={"example": cfg.RepoConfig(anchor=str(state), worktree_root=str(state / "trees"))})
cfg.detect_platform = lambda: "linux"
cfg.load_project_config = lambda *a, **k: config
cfg.load_config = lambda *a, **k: config
cfg.project_dir = lambda *a, **k: project
cfg.tracking_dir = lambda: records
path = records / "host.yaml"
if not path.exists():
    checkout = state / "trees" / "host"
    checkout.mkdir(parents=True, exist_ok=True)
    tracking.create_new_record("host", "worktree/host", str(checkout), "example", "local", "linux", records)
provider = claim_providers.ClaimProviderManifest(
    namespace="bridge-relationship", plugin="agent-bridge@fixture", plugin_root=callback,
    status_command=(sys.executable, callback, base),
)
claim_providers.peer_launch_adapter.explicit_context = lambda: False
claim_providers.discover_claim_providers = lambda *a: ({"bridge-relationship": provider}, ())
if argv == ["probe-finalize"]:
    from agent_worktrees import finalize
    with contextlib.redirect_stdout(sys.stderr):
        settled = finalize._assert_obligations_settled(tracking.load_record(path), "host", abandon=False)
    print(json.dumps({"settled": settled}))
elif argv == ["freeze"]:
    record = tracking.load_record(path)
    record.status = "finalized"
    tracking.save_record(record, path)
    print("{}")
else:
    sys.exit(main.main(argv))
"""

CALLBACK = r"""
import sys
from agent_bridge import __main__ as main
from agent_bridge.client import BridgeClient
base = sys.argv[1]
def client(*, ensure=True):
    assert ensure is False, "claim verification must not ensure a daemon"
    return BridgeClient(base, "synthetic-local-token")
main._get_client = client
main.main(sys.argv[2:])
"""


@pytest.fixture
def command_authority(tmp_path, monkeypatch):
    callback = tmp_path / "provider.py"
    callback.write_text(CALLBACK, encoding="utf-8")
    root = Path(__file__).resolve().parents[3]
    state = tmp_path / "authority"
    state.mkdir()
    base = ["http://127.0.0.1:1"]

    def prefix(*, allow_path_fallback=True):
        return [sys.executable, "-c", BOOTSTRAP, str(root), str(state), str(callback), base[0]]

    monkeypatch.setattr(authority, "_agent_worktrees_launch_prefix", prefix)
    return prefix, base, state


async def serve(manager, base):
    app = FastAPI()
    app.state.session_manager, app.state.resolver, app.state.ready = manager, None, True
    app.include_router(sessions.router)

    @app.get("/health")
    def health():
        return {"protocol_version": HTTP_PROTOCOL_VERSION, "min_protocol_version": 1}

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.setblocking(False)
    base[0] = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="error"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _ in range(100):
            if server.started:
                return server, task, listener
            if task.done():
                await task
                raise RuntimeError("Synthetic Bridge stopped before readiness")
            await asyncio.sleep(0.01)
        raise RuntimeError("Synthetic Bridge did not become ready")
    except BaseException:
        server.should_exit = True
        await task
        listener.close()
        raise


async def probe(prefix, operation):
    from agent_procutil import no_window_kwargs
    proc = await asyncio.create_subprocess_exec(
        *prefix(), operation, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        **no_window_kwargs(),
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), 15)
    assert proc.returncode == 0, stderr.decode(errors="replace") + stdout.decode(errors="replace")
    return json.loads(stdout)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["admission", "retirement"])
async def test_real_commands_bind_visible_obligation_and_registered_local_retirement(
    remote_manager, command_authority, phase,
):
    manager, calls = remote_manager
    prefix, base, _ = command_authority
    server, task, listener = await serve(manager, base)
    try:
        client = BridgeClient(base[0], "synthetic-local-token")
        created = await asyncio.to_thread(
            client.start_session, agent="remote-acp:example",
            caller_owner_ref="local/example/host", caller_id="caller",
        )
        session = manager.get_session(created["session_id"])
        row = manager._db.get_relationship(session.session_id)
        assert created["remote_relationship"]["claim_ref"] == row["claim"]["ref"]
        if phase == "admission":
            inspected = await authority.command("inspect", "local/example/host", {"request_id": row["id"]})
            assert inspected["phase"] == "bound"
            assert (await probe(prefix, "probe-finalize"))["settled"] is False
            assert relationships.verification(manager, inspected["ref"])["relationship_verification"]["retired"] is False
            await manager.stop_session(session.session_id)
            assert manager._db.get_relationship(session.session_id)["phase"] == "bound"
        else:
            await asyncio.to_thread(client.end_session, session.session_id)
            inspected = await authority.command("inspect", "local/example/host", {"request_id": row["id"]})
            assert inspected["phase"] == "closed" and inspected["producer"]["closeout"]["exists"] is True
            assert (await probe(prefix, "probe-finalize"))["settled"] is True
            audit = await asyncio.to_thread(
                client._request, "GET", f"/api/v1/sessions/{session.session_id}/relationship",
            )
            assert audit["ownership"] == "closed" and audit["native_id"] == "server-owned-session"
        assert calls == ["connect", "new"]
    finally:
        server.should_exit = True
        await task
        listener.close()


@pytest.mark.asyncio
async def test_actual_frozen_owner_refuses_before_any_peer_request(remote_manager, command_authority):
    manager, calls = remote_manager
    prefix, _, _ = command_authority
    await probe(prefix, "freeze")
    with pytest.raises(HTTPException) as exc:
        await sessions.start_session(StartSessionRequest(
            agent="remote-acp:example", caller_owner_ref="local/example/host",
        ), _remote_request(manager))
    assert getattr(exc.value, "status_code", None) == 409
    assert calls == []
    session = next(iter(manager._sessions.values()))
    assert manager._db.get_relationship(session.session_id)["phase"] == "pending"
    await manager.end_session(session.session_id)
    audit = manager._db.get_relationship(session.session_id)
    assert audit["phase"] == "closed" and audit["claim"] is None
    assert audit["absence"]["absent"] is True and calls == []
    assert manager.get_session(session.session_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["send", "resume"])
async def test_synthetic_tls_peer_owned_create_send_stop_resume_end(
    peer, tmp_db, command_authority, monkeypatch, operation,
):
    from agent_bridge.remote_acp_ws import WsAcpTransport

    url, context, peer_process = peer
    prefix, base, _ = command_authority
    manager = session_manager_from_config(tmp_db, ServiceConfig(remote_acp={"peer": {
        "endpoint": url + "/owned", "transport": "websocket",
        "auth": {"source": "none", "identity": "synthetic"}, "allow_loopback_test": True,
        "timeout_seconds": 3, "rpc_timeout_seconds": 5,
    }}))
    original = AcpClient.start_remote

    async def start(client, target, *, cookies=None, auth_factory=None):
        wire = WsAcpTransport(target, ssl=context, cookies=cookies)
        await original(client, target, transport=wire, auth_factory=auth_factory)

    monkeypatch.setattr(AcpClient, "start_remote", start)
    server, task, listener = await serve(manager, base)
    try:
        client = BridgeClient(base[0], "synthetic-local-token")
        created = await asyncio.to_thread(
            client.start_session, agent="remote-acp:peer", caller_owner_ref="local/example/host",
        )
        session = manager.get_session(created["session_id"])
        assert session.pid is None and session.target.worktree_id is None
        native_id = session.acp_session_id
        if operation == "send":
            await manager.submit_prompt(session.session_id, "fresh")
            await asyncio.wait_for(session._prompt_task, 5)
        else:
            await manager.stop_session(session.session_id)
            await manager.resume_session(session.session_id)
        assert session.acp_session_id == native_id
        assert (await probe(prefix, "probe-finalize"))["settled"] is False
        await asyncio.to_thread(client.end_session, session.session_id)
        async with httpx.AsyncClient(verify=context, trust_env=False) as http:
            evidence = (await http.get(url + "/evidence")).json()
        assert evidence["created_sessions"] == 1 and evidence["prompts"] == (1 if operation == "send" else 0)
        assert peer_process.poll() is None
        assert manager._db.get_relationship(session.session_id)["phase"] == "closed"
    finally:
        for session in list(manager._sessions.values()):
            await manager.stop_session(session.session_id)
        server.should_exit = True
        await task
        listener.close()
