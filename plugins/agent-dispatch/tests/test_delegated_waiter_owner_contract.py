"""Real isolated owner CLI, HTTP coordinator, and callback subprocess contract."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import sysconfig
import threading
import time

import pytest
import uvicorn
from agent_procutil import no_window_kwargs

from agent_dispatch import delegated_waiter_cli, delegated_waiter_callback, procutil
from agent_dispatch.client import DispatchClient
from agent_dispatch.coordinator import create_app
from agent_dispatch.delegated_waiter_contract import NOTIFICATIONS
from agent_dispatch.hibernation import RunSpec
from tests._delegation_helpers import event, identity, prepare, task
from tests._helpers import RepoDefaultingQueue as TaskQueue

pytestmark = pytest.mark.contract("agent_dispatch.delegated_waiter.owner_contract")


def wait(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    assert predicate()


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[3]
    sources = [root / "plugins" / "agent-pull-requests" / "src",
               root / "libs" / "work-coalescing-singleton" / "src",
               root / "plugins" / "agent-dispatch" / "src"]
    for source in sources:
        monkeypatch.syspath_prepend(str(source))
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(map(str, sources)) +
                       os.pathsep + os.environ.get("PYTHONPATH", ""))
    monkeypatch.setenv("AGENT_PULL_REQUESTS_HOME", str(tmp_path / "owner"))
    for key in tuple(os.environ):
        if key.startswith("AGENT_DISPATCH_"):
            monkeypatch.delenv(key)
    (tmp_path / "coordinator").mkdir()
    monkeypatch.setenv("AGENT_DISPATCH_NO_AUTOSTART", "1")
    monkeypatch.setattr(procutil, "resolve_own_runtime_python", lambda: sys.executable)


@contextmanager
def serving_coordinator(queue, port=0):
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    server = uvicorn.Server(uvicorn.Config(
        create_app(queue, enable_mcp=False), log_level="error", lifespan="off",
    ))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    try:
        thread.start()
        wait(lambda: server.started)
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(timeout=8)
        sock.close()
        assert not thread.is_alive()


@pytest.fixture
def isolated(isolated_state, tmp_path, monkeypatch):
    queue = TaskQueue(tmp_path / "coordinator" / "tasks.db")
    with serving_coordinator(queue) as url:
        monkeypatch.setenv("AGENT_DISPATCH_URL", url)
        yield queue, lambda *_a, **_kw: DispatchClient(url), url


@contextmanager
def serving_owner(fetch):
    from agent_pull_requests.watch_daemon import WatchDaemon, rendezvous_fields, write_lock_data
    from work_coalescing_singleton import CoalescingServer

    owner = WatchDaemon(fetch=fetch, poll_interval=0.02, idle_exit=0.02)
    server = CoalescingServer(owner.compute, on_idle=None)
    try:
        server.start()
        write_lock_data(rendezvous_fields(server))
        yield owner
    finally:
        server.close_admission(reason="isolated test cleanup")
        server.close()
        owner.close()


@pytest.mark.parametrize("state", ["preparing", "active", "replayed", "stale", "invalid_registration"])
@pytest.mark.timeout(60)
def test_actual_callback_exit_codes_and_stale_generation(isolated, state):
    queue, factory, _ = isolated
    task_id = task(queue)
    spec = identity(queue)
    prepare(queue, task_id, spec)
    envelope = {"task_id": task_id, "generation": 1, "delegation": spec}
    fired = event(spec)
    if state != "preparing":
        queue.arm_run_waiter(task_id, generation=1, kind="delegated",
                             delegation={**spec, "registration_id": "registration"})
    if state in ("replayed", "stale"):
        assert delegated_waiter_callback.complete(envelope, fired, factory) == 0
    if state == "stale":
        prepare(queue, task_id, identity(queue, "two"))
    if state == "invalid_registration":
        fired["registration_id"] = "wrong"
    try:
        result = subprocess.run(
            [sys.executable, str(Path(delegated_waiter_callback.__file__)), json.dumps(envelope)],
            input=json.dumps(fired), text=True, capture_output=True,
            timeout=30, check=False, **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"isolated callback timed out: stdout={exc.stdout!r}, stderr={exc.stderr!r}")
    assert result.returncode == (1 if state in ("preparing", "invalid_registration") else 0), result.stderr
    assert len(queue.list_run_waiter_wakes(task_id)) == (0 if result.returncode else 1)
    if state == "stale":
        assert queue.list_pending_run_waiters()[0]["generation"] == 2


@pytest.mark.timeout(120)
def test_real_owner_subscribe_cli_and_two_coalesced_waiters(isolated):
    from agent_pull_requests.watch_contract import PRSnapshot
    from agent_pull_requests.watch_daemon import WatchDaemon, rendezvous_fields, write_lock_data
    from agent_pull_requests.watch_storage import read_subscriptions_state
    from work_coalescing_singleton import CoalescingServer

    queue, factory, _ = isolated
    observed = []
    fire = threading.Event()
    task_ids = []
    owner = WatchDaemon(
        fetch=lambda repo, number: observed.append((repo, number)) or PRSnapshot(merged=fire.is_set()),
        poll_interval=0.02, idle_exit=0.02,
    )
    server = CoalescingServer(owner.compute, on_idle=None)
    server.start()
    write_lock_data(rendezvous_fields(server))
    prefix = [sys.executable, "-m", "agent_pull_requests"]
    try:
        # No authenticated backend fetch: both targets use the same isolated
        # observation key, and real subprocesses use only loopback control APIs.
        for suffix in ("one", "two"):
            task_id = task(queue)
            task_ids.append(task_id)
            prepared = prepare(queue, task_id, identity(queue, suffix))
            result = delegated_waiter_cli.activate(
                None, RunSpec(command=("actual-wait",), task_id=task_id),
                prepared, prefix, factory,
            )
            assert result["detached"]
            assert result["kind"] == "delegated" and "pid" not in result
        assert owner.status()["subscribers"] == {"example/project#7": ["dispatch:one", "dispatch:two"]}
        fire.set()
        wait(lambda: sum(len(queue.list_run_waiter_wakes(t)) for t in task_ids) == 2, timeout=60)
        wait(lambda: owner.status()["subscribers"] == {})
        assert read_subscriptions_state() == []
        assert set(observed) == {("example/project", 7)}
        assert owner.compute("health", {})["capabilities"] == [NOTIFICATIONS]
    finally:
        server.close_admission(reason="isolated test cleanup")
        server.close()
        owner.close()


@pytest.mark.parametrize("module", ["github_provider_adapter", "pr_observation_store"])
def test_lazy_queue_package_import_order_remains_supported(module):
    result = subprocess.run(
        [sys.executable, "-c",
         f"import agent_dispatch.{module}; import agent_dispatch;"
         "assert agent_dispatch.TaskQueue is not None"],
        text=True, capture_output=True, timeout=15, check=False, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("marketplace_index", [0, 1])
@pytest.mark.parametrize("refusal", [None, "foreign_receipt", "inactive"])
@pytest.mark.timeout(60)
def test_cell_callback_reconstructs_original_dispatch_route(
    isolated, tmp_path, marketplace_index, refusal,
):
    from agent_dispatch import peer_launch, rendezvous
    from tests.test_procutil import (
        FIRST_MARKETPLACE_ID, SECOND_MARKETPLACE_ID, _make_live_peer, _make_namespaced_context,
    )
    queue, _, url = isolated
    cells = [tmp_path / "marketplaces" / name
             for name in (FIRST_MARKETPLACE_ID, SECOND_MARKETPLACE_ID)]
    receipts = [_make_namespaced_context(cell) for cell in cells]
    owners = [_make_namespaced_context(cell, plugin_id="agent-pull-requests") for cell in cells]
    selected, foreign = receipts[marketplace_index], receipts[1 - marketplace_index]
    python = _make_live_peer(cells[marketplace_index], "agent-dispatch", "1.0.0")
    slot = python.parent.parent
    site = (slot / "Lib" / "site-packages" if os.name == "nt" else
            slot / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages")
    shutil.copytree(
        Path(delegated_waiter_callback.__file__).parent, site / "agent_dispatch",
        dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"),
    )
    (site / "dependencies.pth").write_text(
        f"import site; site.addsitedir({sysconfig.get_path('purelib')!r})\n", encoding="utf-8",
    )
    rendezvous.write_endpoint(selected.parent / "run", "tcp", url.removeprefix("http://"))
    rendezvous.write_endpoint(foreign.parent / "run", "tcp", "127.0.0.1:1")
    task_id = task(queue)
    spec = {**identity(queue), "installation": {
        "root": str(selected.parent), "receipt": str(selected),
    }}
    prepare(queue, task_id, spec)
    queue.arm_run_waiter(task_id, generation=1, kind="delegated",
                         delegation={**spec, "registration_id": "registration"})
    callback = delegated_waiter_cli.callback_argv(task_id, 1, spec)
    owner = peer_launch.validate_owner(
        "agent-pull-requests", owners[marketplace_index].parent, str(owners[marketplace_index]),
    )
    environment = peer_launch.peer_environment(owner, dict(os.environ))
    environment.update(
        AGENT_DISPATCH_INSTALL_DIR=str(foreign.parent),
        AGENT_DISPATCH_URL="http://127.0.0.1:1",
        AGENT_DISPATCH_TOKEN="synthetic-foreign-credential",
    )
    if refusal == "foreign_receipt":
        spec["installation"]["receipt"] = str(foreign)
    elif refusal == "inactive":
        value = json.loads(selected.read_text(encoding="utf-8"))
        value["state"] = "inactive"
        selected.write_text(json.dumps(value), encoding="utf-8")
    protected_paths = [*receipts, *owners, foreign.parent / "run" / "endpoint.json"]
    protected = {path: path.read_bytes() for path in protected_paths}
    envelope = {"task_id": task_id, "generation": 1, "delegation": spec}
    callback[-1] = json.dumps(envelope)
    for _ in range(2):
        result = subprocess.run(
            callback,
            env=environment, input=json.dumps(event(spec)), text=True,
            capture_output=True, timeout=30, check=False, **no_window_kwargs(),
        )
        assert (result.returncode == 0) == (refusal is None), result.stderr
    assert len(queue.list_run_waiter_wakes(task_id)) == (0 if refusal else 1)
    assert {path: path.read_bytes() for path in protected_paths} == protected


@pytest.mark.parametrize("timed_out", [False, True])
@pytest.mark.timeout(120)
def test_owner_and_coordinator_restart_replay_pending_delivery(
    isolated_state, tmp_path, monkeypatch, timed_out,
):
    from agent_pull_requests.watch_contract import PRSnapshot
    from agent_pull_requests.watch_storage import read_subscriptions_state

    database = tmp_path / "coordinator" / "tasks.db"
    queue = TaskQueue(database)
    fire = threading.Event()
    fetch = lambda *_: PRSnapshot(merged=fire.is_set())
    with serving_owner(fetch) as owner:
        with serving_coordinator(queue) as url:
            monkeypatch.setenv("AGENT_DISPATCH_URL", url)
            factory = lambda *_a, **_kw: DispatchClient(url)
            task_id = task(queue)
            spec = {**identity(queue), "timeout": 5.0 if timed_out else 300.0}
            prepared = prepare(queue, task_id, spec)
            result = delegated_waiter_cli.activate(
                None, RunSpec(command=("actual-wait",), task_id=task_id),
                prepared, [sys.executable, "-m", "agent_pull_requests"], factory,
            )
            assert result["detached"]
        assert not queue.list_run_waiter_wakes(task_id)
        if not timed_out:
            fire.set()
        wait(lambda: any(
            (entry.get("pending") or {}).get("attempts", 0) > 0
            for entry in read_subscriptions_state()
        ), timeout=60)
        pending = read_subscriptions_state()
        assert len(pending) == 1
        payload = pending[0]["pending"]["payload"]
        assert payload["timed_out"] is timed_out
        assert pending[0]["pending"]["last_error"] == "callback_nonzero"
        assert not queue.list_run_waiter_wakes(task_id)
    restored = TaskQueue(database)
    assert restored.run_waiter_coordinator_id() == spec["coordinator_id"]
    with serving_owner(fetch) as owner:
        assert owner.status()["pending_deliveries"][0]["event_id"] == payload["event_id"]
        port = int(url.rsplit(":", 1)[1])
        with serving_coordinator(restored, port=port) as resumed_url:
            assert resumed_url == url
            wait(lambda: len(restored.list_run_waiter_wakes(task_id)) == 1, timeout=60)
            wait(lambda: owner.status()["subscribers"] == {})
            assert read_subscriptions_state() == []
            replay = subprocess.run(
                pending[0]["notify"]["argv"], input=json.dumps(payload), text=True,
                capture_output=True, timeout=30, check=False, **no_window_kwargs(),
            )
            assert replay.returncode == 0, replay.stderr
            assert len(restored.list_run_waiter_wakes(task_id)) == 1
            waiter = restored.find_delegated_preparation(task_id, spec["preparation_id"])
            assert waiter["event_id"] == payload["event_id"]
