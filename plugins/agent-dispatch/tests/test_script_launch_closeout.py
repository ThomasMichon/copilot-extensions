"""A disposable script and HTTP coordinator exercise the actual supervisor launch."""

import argparse
import json
import os
import socket
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import pytest
import uvicorn

from agent_dispatch.__main__ import _client
from agent_dispatch.client import DispatchClient, ResolvingDispatchClient
from agent_dispatch.coordinator import create_app
from agent_dispatch.create_cli import _spawn_route
from agent_dispatch.managed_retention import process_identity_domain
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_common import Task
from agent_dispatch.spawn_factories import make_label_routed_spawn
from agent_dispatch.spawn_script_body import (
    _parse_script_body_handle,
    exact_script_exit,
    make_script_spawn,
)
from agent_dispatch.supervisor import Supervisor


def wait_for(predicate: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 15
    while not predicate():
        assert time.monotonic() < deadline, "timed out waiting for isolated worker"
        time.sleep(0.02)


@pytest.fixture
def coordinator(tmp_path: Path) -> Iterator[tuple[TaskQueue, str]]:
    queue = TaskQueue(tmp_path / "queue.db")
    app = create_app(
        queue, token="fixture-data", control_token="fixture-control", enable_mcp=False
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, lifespan="off", ws="none", log_level="error"))
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
        thread.start()
        try:
            wait_for(lambda: server.started)
            yield queue, f"http://127.0.0.1:{sock.getsockname()[1]}"
        finally:
            server.should_exit = True
            thread.join(10)
            assert not thread.is_alive()


@pytest.mark.parametrize("early_exit", [False, True])
def test_real_script_failure_before_and_after_publication(
    coordinator: tuple[TaskQueue, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    early_exit: bool,
) -> None:
    queue, url = coordinator
    # Run the child's actual runtime client, without inheriting operator routing or credentials.
    safe_env = {k: os.environ[k] for k in ("PATH", "SystemRoot", "WINDIR") if k in os.environ}
    safe_env.update(
        HOME=str(tmp_path),
        USERPROFILE=str(tmp_path),
        TMPDIR=str(tmp_path),
        PYTHONPATH=os.pathsep.join(map(os.path.abspath, sys.path)),
        PYTHONDONTWRITEBYTECODE="1",
        AGENT_DISPATCH_SHARED_URL=url,
        AGENT_DISPATCH_SHARED_TOKEN="fixture-data",
    )
    monkeypatch.setattr(os, "environ", safe_env)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    child = tmp_path / "fixture_worker.py"
    child.write_text(
        "import sys, time\nfrom pathlib import Path\n"
        "from agent_dispatch.script_worker import ScriptTaskRuntime\n"
        "with ScriptTaskRuntime.from_env() as runtime:\n"
        "    runtime.announce_start()\n"
        "    if sys.argv[1] == 'early': raise ValueError('early failure')\n"
        "    runtime.report_failure('reported failure')\n"
        "    deadline = time.monotonic() + 15\n"
        "    while not Path('exit').exists() and time.monotonic() < deadline: time.sleep(.02)\n",
        encoding="utf-8",
    )
    task = queue.create(
        "script",
        repo="example",
        labels=["script"],
        payload_inline=json.dumps(
            {
                "path": str(child),
                "args": ["early" if early_exit else "late"],
                "cwd": str(tmp_path),
            }
        ),
    )
    assert isinstance(task, Task)
    args = argparse.Namespace(shared=True, control_token="fixture-control")
    with ResolvingDispatchClient(lambda: _client(args, ensure=False)) as resolving:
        client = cast(DispatchClient, resolving)
        spawn = make_script_spawn(route=_spawn_route(args))
        if early_exit:
            spawn = make_label_routed_spawn(
                lambda _: pytest.fail("unexpected non-script body"), overrides={"script": spawn}
            )
        supervisor = Supervisor(
            client,
            repo="example",
            supervisor_id="fixture-lane",
            machine="fixture",
            spawn_fn=spawn,
            nudge=False,
        )
        publish = client.record_spawn

        def publish_after_exit(key: str, **fields: Any) -> dict[str, Any]:
            parsed = _parse_script_body_handle(fields["session_handle"])
            assert parsed
            wait_for(lambda: exact_script_exit(parsed[1], parsed[2]))
            before = queue.get_reservation(key)
            assert before and before.state == "reserving" and not before.startup_complete
            return publish(key, **fields)

        if early_exit:
            monkeypatch.setattr(client, "record_spawn", publish_after_exit)
        try:
            assert supervisor.poll_once() == [task.id]
            row = queue.latest_reservation(task.id)
            assert (
                row and row.startup_complete and row.startup_revision == 1 and row.session_handle
            )
            parsed = _parse_script_body_handle(row.session_handle)
            assert parsed and parsed[2]
            assert (
                row.allocation_project,
                row.worktree,
                row.worktree_ownership,
                row.worktree_path,
            ) == (None,) * 4
            assert row.allocation_reservation_key == row.key
            facts = json.loads(row.session_handle.partition(":")[2])
            assert facts["process_domain"] == process_identity_domain()
            assert facts["queued_generation"] == task.generation

            def reported() -> bool:
                current = queue.get(task.id)
                return bool(
                    current
                    and current.latest_progress
                    and json.loads(current.latest_progress)["phase"] == "script-failure"
                )

            wait_for(reported)
            owned = queue.get(task.id)
            assert owned and (owned.status, owned.owner, owned.generation) == (
                "started",
                parsed[0],
                task.generation + 1,
            )
            if not early_exit:
                assert supervisor.recover_gone() == 0
                (tmp_path / "exit").touch()
            wait_for(lambda: exact_script_exit(parsed[1], parsed[2]))
            assert supervisor.recover_gone() == 1
            ended, retired = queue.get(task.id), queue.get_reservation(row.key)
            assert ended and ended.status == "abandoned" and not ended.owner
            assert retired and retired.state == "settled" and retired.conversation_retired
            assert retired.detail == (
                "ValueError: early failure" if early_exit else "reported failure"
            )
            assert supervisor.recover_gone() == 0
        finally:
            (tmp_path / "exit").touch()
