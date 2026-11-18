"""Model-free ACP load crosses the actual selected Worktrees parser process."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from agent_procutil import direct_python, no_window_kwargs

from agent_bridge import acquisition_worktree_writer as writers, retained_load_acceptance as acceptance
from agent_bridge import worktree_lineage
from agent_bridge.acquisition_contract import WorktreeWriterRequirement
from agent_bridge.connect import ConnectTracker
from agent_bridge.creation_worktree_writer import CreationWriterScope, JOURNAL_KEY
from agent_bridge.events import EventLog
from agent_bridge.models import RetainedRecoveryRequest
from agent_bridge.retained_session_recovery import RetainedRecovery
from agent_bridge.session_manager import Session
from agent_bridge.session_status import SessionStatus
from agent_bridge.transport import SpawnTarget

NATIVE = "11111111-2222-4333-8444-555555555555"
pytestmark = pytest.mark.contract("agent_bridge.retained_hosted_registration")


@pytest.mark.asyncio
async def test_acknowledged_hosted_load_registers_without_a_cli_hold(
    session_manager: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not sys.platform.startswith("linux"):
        pytest.skip("Hosted activation is qualified against Linux procfs")
    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root / "plugins/agent-worktrees/src"))
    for library in ("lazy-cli-dispatch", "work-coalescing-singleton", "update-telemetry"):
        monkeypatch.syspath_prepend(str(root / "libs" / library / "src"))
    from agent_worktrees import tracking, session_liveness, config as cfg, config_authority
    from agent_worktrees import tracking_session_registration_write as registration
    from agent_worktrees import tracking_session_deregistration_write as deregistration

    state = tmp_path / "authority"
    state.mkdir()
    identity = {"installation_root": str(state / "owner"), "runtime_generation": "a" * 32}
    (state / "writer-identity.json").write_text(json.dumps(identity), encoding="utf-8")
    interpreter, environment = direct_python(sys.executable)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    prefix = (
        interpreter, str(root / "plugins/agent-worktrees/tests/fixtures/worktree_record_owner.py"),
        str(root), str(state), "example", "host",
    )
    protected = subprocess.run(
        [*prefix, "protect"], capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert protected.returncode == 0, protected.stderr + protected.stdout
    path = state / "project/worktrees/host.yaml"
    config = cfg.Config(
        srcroot=str(state), machine="local", platform="linux", repo_name="example",
        repos={"example": cfg.RepoConfig(
            anchor=str(state), worktree_root=str(state / "trees"),
        )},
    )
    monkeypatch.setattr(config_authority, "load_project_authority_config", lambda *_a, **_kw: config)
    monkeypatch.setattr(cfg, "active_project", lambda: "example")
    monkeypatch.setattr(cfg, "project_dir", lambda *_a, **_kw: state / "project")
    monkeypatch.setattr(cfg, "tracking_dir", lambda *_a, **_kw: path.parent)
    monkeypatch.setattr(cfg, "load_project_config", lambda *_a, **_kw: config)
    monkeypatch.setattr(tracking, "_owning_tracking_dir", lambda _: path.parent)
    original = {
        "worktree_id": "host", "yaml_path": str(path), "session_id": NATIVE,
        "source": "hook:new", "started_at": "first", "source_identity": "native:original",
    }
    assert registration.apply_session_register(original)["ok"]
    assert deregistration.apply_session_deregister({
        **original, "ended_at": "original-exit",
    })["lifecycle"] == "ended"
    before = tracking.load_record(path)
    checkout = Path(before.worktree_path)
    (checkout / ".git").mkdir()
    policy = checkout / ".copilot-extensions/agent-worktrees/config.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text("expose_agent: true\n", encoding="utf-8")
    transcript = tmp_path / "acp.jsonl"

    async def resolve(*_args: Any, **_kwargs: Any) -> Any:
        return [
            interpreter, str(Path(__file__).with_name("cold_drain_peer.py")),
            str(transcript), NATIVE,
        ], str(checkout), dict(os.environ)

    monkeypatch.setattr("agent_bridge.transport.resolve_local_launch", resolve)
    monkeypatch.setattr("agent_bridge.local_static_preparation.prepare_for_launch", AsyncMock())
    monkeypatch.setattr(worktree_lineage, "_agent_worktrees_launch_prefix", lambda: list(prefix))
    requirement = WorktreeWriterRequirement.model_validate({
        "version": 1, "identity": identity, "creation_nonce": before.creation_nonce,
    })
    writer = writers.RetainedWriter(
        CreationWriterScope("a" * 64, "example", "host", str(checkout), requirement), prefix,
    )
    monkeypatch.setattr(writers, "current", lambda: writer)
    manager = session_manager
    target = SpawnTarget(
        type="local", cwd=str(checkout), project="example", worktree_id="host",
        venue={JOURNAL_KEY: "a" * 64},
    )
    session = Session("original-control", "Original", target)
    session.acp_session_id, session.status = NATIVE, SessionStatus.STOPPED
    manager.db.create_session(
        session.session_id, session.name, None, target.cwd, "local", "stopped", time.time(),
        acp_session_id=NATIVE, target_json=target.to_json(),
    )
    session.event_log = EventLog(db=manager.db, session_id=session.session_id)
    manager._sessions[session.session_id] = session
    request = RetainedRecoveryRequest(
        operation_id="original-load", journal_key="a" * 64, native_session_id=NATIVE,
        head_revision=before.head_revision, bridge_installation=str(tmp_path / "bridge"),
    )
    owner = RetainedRecovery(manager, session, request, target.to_json(), asyncio.current_task())
    # This boundary test supplies an admitted operation. SQL operation admission
    # and stop serialization remain covered by retained-recovery controls.
    monkeypatch.setattr(owner, "require_current", Mock())
    owner.hosted_epoch = acceptance.epoch()
    client = None
    try:
        client, native = await manager._connect_via_session_host(
            target, tracker=ConnectTracker(), session_id=session.session_id,
            on_acp_event=lambda event, data: manager._route_acp_event(session, event, data),
            permission_callback=None, load_session_id=NATIVE, recovery_operation=owner,
        )
        assert native == NATIVE and owner.accepted_load is not None
        activation = acceptance.registration(owner, client)
        child = activation["child"]["pid"]
        assert not session_liveness.session_process_owns_hold(
            NATIVE, child, activation["child"]["start_time"],
        )
        assert worktree_lineage.register_session(
            "host", NATIVE, pid=child, worktree_dir=str(checkout), hosted_activation=activation,
        )
        registered = path.read_bytes()
        assert worktree_lineage.register_session(
            "host", NATIVE, pid=child, worktree_dir=str(checkout), hosted_activation=activation,
        )
        assert path.read_bytes() == registered
        after = tracking.load_record(path)
        entry = after.session_entry(NATIVE)
        previous = before.session_entry(NATIVE)
        assert previous is not None
        assert entry is not None and len(entry.activations) == 2
        assert entry.activations[0] == previous.activations[0]
        assert entry.activations[1].start_source == "retained-hosted-acp"
        assert entry.activations[1].ended_at is None and entry.pid == child
        assert after.creation_nonce == before.creation_nonce
        assert not (state / "resident-calls").exists()
        original_host = manager._host_index.get(session.session_id)
        manager._set_background_recovery_enabled(session, False)
        await client.shutdown()
        session.client = None
        assert await manager._reattach_one(
            original_host, session, new_status=SessionStatus.IDLE,
        )
        client = manager.get_session(session.session_id).client
        assert client is not None
        assert client.acp_session_id == NATIVE
        assert acceptance.host_binding(manager._host_index.get(session.session_id)) == acceptance.host_binding(
            original_host,
        )
        assert path.read_bytes() == registered
        messages = [json.loads(line) for line in transcript.read_text().splitlines()]
        assert sum("boot" in item for item in messages) == 1
        assert [item["method"] for item in messages if "method" in item] == [
            "initialize", "session/load", "initialize",
        ]
        assert not manager.db.execute_read("SELECT * FROM turns")
    finally:
        if client is not None:
            await client.shutdown()
        for host in manager._host_index.all():
            manager._reap_host_record(host, "owned hosted-registration fixture cleanup")
