"""Offline ACP fixture crosses the real process, protocol and SQLite boundaries."""

from __future__ import annotations

import json
import sys
from unittest.mock import AsyncMock

import pytest

from agent_bridge.acquisition_contract import AcquisitionRefused
from agent_bridge.transport import SpawnTarget
from agent_bridge.workspace_creation import start

_AGENT = """
import json, os, sys, uuid
ledger = sys.argv[1]
def record(event):
    with open(ledger, 'a', encoding='utf-8') as output:
        output.write(json.dumps(event) + '\\n')
        output.flush()
        os.fsync(output.fileno())
for line in sys.stdin:
    request = json.loads(line)
    method = request.get('method')
    if 'id' not in request:
        continue
    if method == 'initialize':
        result = {'protocolVersion': 1, 'agentCapabilities': {'loadSession': True}}
    elif method == 'session/new':
        native = str(uuid.uuid4())
        record({'native': native, 'method': method})
        result = {'sessionId': native}
    elif method == 'session/load':
        params = request['params']
        native = params['sessionId']
        with open(ledger, encoding='utf-8') as history:
            assert any(json.loads(line)['native'] == native for line in history)
        record({'native': native, 'method': method, 'cwd': params['cwd']})
        result = {}
    else:
        print(json.dumps({'jsonrpc': '2.0', 'id': request['id'],
                          'error': {'code': -32601, 'message': 'unsupported fixture method'}}),
              flush=True)
        continue
    print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}), flush=True)
"""


@pytest.mark.asyncio
async def test_provider_key_replays_one_real_stdio_native(session_manager, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agent_bridge.provider_hosting.query_provider_policy",
        AsyncMock(return_value={
            "version": 1, "allowed": True, "repository": str(tmp_path),
            "expose_agent": None, "sources": [], "denied_by": [],
        }),
    )
    program = tmp_path / "fixture_agent.py"
    ledger = tmp_path / "native-events.jsonl"
    program.write_text(_AGENT, encoding="utf-8")
    target = SpawnTarget(
        type="command", spawn_command=[sys.executable, str(program), str(ledger)],
        venue={
            "provider": "fixture-provider", "kind": "fixture",
            "target_id": "fixture:original", "instance_id": "original",
            "scope": "provider-instance", "transport": "provider-exec", "ready": True,
            "workspace_folder": str(tmp_path),
            "capabilities": {"instance_pinned_exec": True},
        },
    )
    session = None
    try:
        session, first = await start(
            session_manager, target, key="logical-work", provider_instance=True,
        )
        replay, receipt = await start(
            session_manager, target, key="logical-work", provider_instance=True,
        )
        records = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
        assert records == [{"native": session.acp_session_id, "method": "session/new"}]
        assert replay is session and receipt["replayed"]
        assert first["native_session_id"] == receipt["native_session_id"] == records[0]["native"]
        assert not session_manager.db.execute_read("SELECT * FROM turns")
        assert not session_manager.db.execute_read("SELECT * FROM workspace_embodiments")
        await session_manager.stop_session(session.session_id)
        resumed = await session_manager.resume_session(session.session_id, drain=False)
        assert resumed is session and resumed.target.cwd is None
        loaded = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
        assert loaded == [
            *records, {"native": first["native_session_id"], "method": "session/load", "cwd": str(tmp_path)},
        ]
        await session_manager.end_session(session.session_id, force=True)
        with pytest.raises(AcquisitionRefused) as refused:
            await start(session_manager, target, key="logical-work", provider_instance=True)
        assert refused.value.code == "creation_pending"
        assert len(ledger.read_text(encoding="utf-8").splitlines()) == 2
    finally:
        if session is not None and session_manager.get_session(session.session_id) is not None:
            await session_manager.end_session(session.session_id, force=True)
