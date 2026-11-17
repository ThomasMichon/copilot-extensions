"""Independent metadata receivers share one original-journal commit boundary."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
from typing import Any

from agent_procutil import no_window_flags
import pytest

from agent_bridge import retained_metadata_recovery as metadata
from agent_bridge.session_manager import SessionManager
from test_retained_metadata_recovery import missing_record
from test_creation_worktree_writer import writer_creation  # noqa: F401
from test_workspace_creation import creation_rig, rig as _rig  # noqa: F401

rig = _rig
pytestmark = pytest.mark.contract("agent_bridge.retained_metadata_recovery")


@pytest.mark.asyncio
async def test_independent_receivers_commit_one_epoch_and_newer_writer_contract(
    writer_creation: Any, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    fixture, manager = writer_creation, writer_creation.rig.manager
    old, request = await missing_record(fixture, monkeypatch)
    worker = Path(__file__).with_name("_retained_metadata_worker.py")
    children: list[asyncio.subprocess.Process] = []
    try:
        for _ in range(2):
            child = await asyncio.create_subprocess_exec(
                sys.executable, str(worker), str(manager.db.db_path),
                request.model_dump_json(), fixture.requirement.model_dump_json(),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, creationflags=no_window_flags(),
            )
            children.append(child)
            assert child.stdout is not None
            assert await asyncio.wait_for(child.stdout.readline(), 15) == b"ready\n"
        for child in children:
            assert child.stdin is not None
            child.stdin.write(b"start\n")
            await child.stdin.drain()
        completed = await asyncio.wait_for(asyncio.gather(
            *(child.communicate() for child in children),
        ), 30)
        results = []
        for child, (stdout, stderr) in zip(children, completed):
            assert child.returncode == 0, stderr.decode("utf-8", errors="replace")
            results.append(json.loads(stdout))
        committed = [item for item in results if item["state"] == "committed"]
        assert len(committed) == 1
        assert all(
            item["state"] == "committed" or item["code"] in {"executor_live", "retained_metadata_held"}
            for item in results
        )
        restarted = SessionManager(manager.db, read_only_rehydrate=True)
        adopted, boundary = await metadata.prepare(
            restarted, old.session_id, request, fixture.requirement, lambda: True,
        )
        assert boundary == committed[0]["boundary"]
        assert adopted.session_id == committed[0]["session_id"]
        assert adopted.acp_session_id == old.acp_session_id
        assert len(manager.db.list_sessions()) == 1
        assert restarted.get_session(old.session_id) is None
        assert restarted.get_session(old.acp_session_id) is None
        assert not manager.db.execute_read("SELECT * FROM turns")
    finally:
        for child in children:
            if child.returncode is None:
                child.terminate()
                await asyncio.wait_for(child.wait(), 10)
