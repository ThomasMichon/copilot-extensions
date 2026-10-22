"""Real isolated owner CLI, HTTP coordinator, and callback subprocess contract."""

from __future__ import annotations

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
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
import venv

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


def install_callback_entry(root):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    (root / "bin").mkdir(exist_ok=True)
    for name in (
        "resolve-runtime.ps1", "resolve-runtime.sh",
        "delegated-waiter-callback.ps1", "delegated-waiter-callback.sh",
    ):
        shutil.copyfile(scripts / name, root / "bin" / name)


def install_callback_runtime(root, version):
    slot = root / "versions" / version
    venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(slot)
    site = (slot / "Lib" / "site-packages" if os.name == "nt" else
            slot / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages")
    shutil.copytree(
        Path(delegated_waiter_callback.__file__).parent, site / "agent_dispatch",
        dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"),
    )
    (site / "dependencies.pth").write_text(
        f"import site; site.addsitedir({sysconfig.get_path('purelib')!r})\n", encoding="utf-8",
    )
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": version, "completed_at": "2026-01-01T00:00:00Z", "pid": 1,
    }), encoding="utf-8")
    (root / "current-version").write_text(version, encoding="utf-8")
    return slot


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
    runtime = tmp_path / "dispatch runtime"
    runtime.mkdir()
    install_callback_entry(runtime)
    install_callback_runtime(runtime, "1.0.0")
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(runtime))
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
        from agent_dispatch import rendezvous
        rendezvous.write_endpoint(
            Path(os.environ["AGENT_DISPATCH_INSTALL_DIR"]) / "run",
            "tcp", f"127.0.0.1:{sock.getsockname()[1]}",
        )
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
    envelope = {
        "task_id": task_id, "generation": 1, "delegation": spec,
        "legacy_root": os.environ["AGENT_DISPATCH_INSTALL_DIR"],
    }
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


@pytest.mark.timeout(60)
def test_legacy_owner_prefix_ignores_pythonpath_and_working_directory(tmp_path, monkeypatch):
    from tests.test_procutil import FIRST_MARKETPLACE_ID, _make_live_peer

    cell = tmp_path / "marketplaces" / FIRST_MARKETPLACE_ID
    python = _make_live_peer(cell, "agent-pull-requests", "1.0.0")
    legacy = tmp_path / ".agent-pull-requests"
    shutil.move(str(python.parent.parent.parent.parent), legacy)
    shadow = tmp_path / "shadow"
    package = shadow / "agent_pull_requests"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("raise AssertionError('shadow imported')", encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setenv("PYTHONPATH", str(shadow))
    prefix = procutil.agent_pull_requests_launch_prefix()
    assert prefix is not None and prefix[1:] == ["-I", "-m", "agent_pull_requests"]
    result = subprocess.run(
        [*prefix, "health"], cwd=shadow, text=True, capture_output=True,
        timeout=15, check=False, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["isolated"] == 1


@pytest.mark.timeout(60)
def test_legacy_callback_discards_foreign_resident_dispatch_route(isolated, tmp_path, monkeypatch):
    queue, _, _ = isolated
    task_id = task(queue)
    spec = identity(queue)
    prepare(queue, task_id, spec)
    queue.arm_run_waiter(task_id, generation=1, kind="delegated",
                         delegation={**spec, "registration_id": "registration"})
    callback = delegated_waiter_cli.callback_argv(task_id, 1, spec)
    foreign = TaskQueue(tmp_path / "foreign.db")
    with monkeypatch.context() as resident:
        resident.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path / "foreign"))
        with serving_coordinator(foreign) as foreign_url:
            resident.setenv("AGENT_DISPATCH_URL", foreign_url)
            resident.setenv("AGENT_DISPATCH_TOKEN", "synthetic-foreign-token")
            resident.setenv("AGENT_DISPATCH_TASK_AUTHORITY", "central")
            resident.setenv("AGENT_DISPATCH_ENDPOINT", "tcp:127.0.0.1:1")
            for _ in range(2):
                result = subprocess.run(
                    callback, input=json.dumps(event(spec)), text=True, capture_output=True,
                    timeout=30, check=False, **no_window_kwargs(),
                )
                assert result.returncode == 0, result.stderr
    assert len(queue.list_run_waiter_wakes(task_id)) == 1
    assert not foreign.list_run_waiter_wakes(task_id)


@pytest.mark.skipif(os.name != "nt", reason="Windows callback wrapper and Job cleanup contract")
@pytest.mark.timeout(60)
def test_notification_timeout_retires_callback_descendants_before_replay(isolated, tmp_path, monkeypatch):
    import ctypes
    from ctypes import wintypes
    from agent_pull_requests.watch_contract import PRSnapshot
    from agent_pull_requests.watch_notification import default_notify
    from agent_pull_requests.watch_registry import WatchKey, WatchRegistry

    queue, _, _ = isolated
    task_id = task(queue)
    spec = identity(queue)
    prepare(queue, task_id, spec)
    registry = WatchRegistry()
    key = WatchKey(spec["repo"], spec["number"])
    registry.register(
        key, spec["subscriber_id"], until=tuple(spec["until"]), acknowledged=True,
        notify={"argv": delegated_waiter_cli.callback_argv(task_id, 1, spec), "timeout": 10},
    )
    fired = registry.apply_snapshot(key, PRSnapshot(merged=True))[0]
    queue.arm_run_waiter(
        task_id, generation=1, kind="delegated",
        delegation={**spec, "registration_id": fired.subscriber.registration_id},
    )
    root = Path(os.environ["AGENT_DISPATCH_INSTALL_DIR"])
    callback = root / "versions" / "1.0.0" / "Lib" / "site-packages" / "agent_dispatch" / "delegated_waiter_callback.py"
    original = callback.read_text(encoding="utf-8")
    stalled = """
import os, pathlib, subprocess, time
from agent_procutil import no_window_kwargs
if os.environ.get("CALLBACK_STALL_MARKER"):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                             **no_window_kwargs())
    marker = pathlib.Path(os.environ["CALLBACK_STALL_MARKER"])
    staged = marker.with_suffix(".tmp")
    staged.write_text(json.dumps([os.getppid(), os.getpid(), child.pid]), encoding="utf-8")
    staged.replace(marker)
    time.sleep(60)
"""
    callback.write_text(original.replace("import httpx\n", stalled + "\nimport httpx\n"), encoding="utf-8")
    marker = tmp_path / "stalled.json"
    monkeypatch.setenv("CALLBACK_STALL_MARKER", str(marker))
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handles = []
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            attempt = pool.submit(default_notify, fired)
            wait(lambda: marker.exists() or attempt.done())
            assert marker.exists()
            for pid in json.loads(marker.read_text(encoding="utf-8")):
                handle = kernel.OpenProcess(0x1000 | 0x100000 | 0x0001, False, pid)
                assert handle, ctypes.get_last_error()
                handles.append(handle)
            with pytest.raises(subprocess.TimeoutExpired):
                attempt.result(timeout=20)
        assert all(kernel.WaitForSingleObject(handle, 0) == 0 for handle in handles)
        assert not queue.list_run_waiter_wakes(task_id)
        monkeypatch.delenv("CALLBACK_STALL_MARKER")
        assert default_notify(fired) == 0
        assert len(queue.list_run_waiter_wakes(task_id)) == 1
    finally:
        for handle in handles:
            if kernel.WaitForSingleObject(handle, 0) == 0x102:
                kernel.TerminateProcess(handle, 137)
                assert kernel.WaitForSingleObject(handle, 5000) == 0
            kernel.CloseHandle(handle)


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
    install_callback_entry(selected.parent)
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
    callback[-1] = "hex:" + json.dumps(envelope).encode("utf-8").hex()
    for _ in range(2):
        result = subprocess.run(
            callback,
            env=environment, input=json.dumps(event(spec)), text=True,
            capture_output=True, timeout=30, check=False, **no_window_kwargs(),
        )
        assert (result.returncode == 0) == (refusal is None), result.stderr
    assert len(queue.list_run_waiter_wakes(task_id)) == (0 if refusal else 1)
    assert {path: path.read_bytes() for path in protected_paths} == protected


@pytest.mark.parametrize("cell_index", [None, 0, 1])
@pytest.mark.timeout(120)
def test_pending_callback_survives_slot_upgrade_and_garbage_collection(
    isolated, tmp_path, monkeypatch, cell_index,
):
    from agent_dispatch import peer_launch, rendezvous
    from agent_pull_requests.watch_contract import PRSnapshot
    from agent_pull_requests.watch_storage import read_subscriptions_state
    from tests.test_procutil import (
        FIRST_MARKETPLACE_ID, SECOND_MARKETPLACE_ID, _make_live_peer, _make_namespaced_context,
    )

    queue, factory, url = isolated
    installation = None
    if cell_index is None:
        root = Path(os.environ["AGENT_DISPATCH_INSTALL_DIR"])
    else:
        cell = tmp_path / "marketplaces" / (FIRST_MARKETPLACE_ID, SECOND_MARKETPLACE_ID)[cell_index]
        receipt = _make_namespaced_context(cell)
        owner_receipt = _make_namespaced_context(cell, plugin_id="agent-pull-requests")
        root = receipt.parent
        _make_live_peer(cell, "agent-dispatch", "1.0.0")
        install_callback_runtime(root, "1.0.0")
        install_callback_entry(root)
        installation = {"root": str(root), "receipt": str(receipt)}
        rendezvous.write_endpoint(root / "run", "tcp", url.removeprefix("http://"))
        # The daemon delivers from the PR installation, not the subscriber's
        # Dispatch environment; canonical peer reentry must restore the latter.
        owner_context = peer_launch.validate_owner(
            "agent-pull-requests", owner_receipt.parent, str(owner_receipt),
        )
        environment = peer_launch.peer_environment(owner_context, dict(os.environ))
        # The subscription CLI is source integration; the callback itself
        # resolves an isolated installed runtime and ignores PYTHONPATH.
        environment["PYTHONPATH"] = os.environ["PYTHONPATH"]
        for key in tuple(os.environ):
            if key not in environment:
                monkeypatch.delenv(key)
        for key, value in environment.items():
            monkeypatch.setenv(key, value)
    task_id = task(queue)
    spec = identity(queue)
    if installation is not None:
        spec["installation"] = installation
    prepared = prepare(queue, task_id, spec)
    fire = threading.Event()
    launches = []
    run_capture = procutil.run_background_capture

    def capture_launch(*args, **kwargs):
        # The source CLI is not an installed PR runtime. Only its loopback
        # subscription request uses legacy invocation; delivery below retains
        # the actual PR cell and must cross canonical peer governance.
        environment = dict(os.environ)
        for key in ("COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT", "AGENT_RT_ROOT"):
            environment.pop(key, None)
        kwargs["env"] = environment
        result = run_capture(*args, **kwargs)
        launches.append(result)
        return result

    monkeypatch.setattr(procutil, "run_background_capture", capture_launch)
    with serving_owner(lambda *_: PRSnapshot(merged=fire.is_set())) as owner:
        result = delegated_waiter_cli.activate(
            None, RunSpec(command=("actual-wait",), task_id=task_id), prepared,
            [sys.executable, "-m", "agent_pull_requests"], factory,
        )
        assert result["detached"], (result, launches)
        pending = read_subscriptions_state()
        assert len(pending) == 1 and pending[0].get("pending") is None
        argv = pending[0]["notify"]["argv"]
        old_slot = root / "versions" / "1.0.0"
        assert all(str(old_slot) not in argument for argument in argv)
        install_callback_runtime(root, "2.0.0")
        shutil.rmtree(old_slot)
        assert not old_slot.exists()
        fire.set()
        wait(lambda: len(queue.list_run_waiter_wakes(task_id)) == 1, timeout=60)
        wait(lambda: owner.status()["subscribers"] == {})
        waiter = queue.find_delegated_preparation(task_id, spec["preparation_id"])
        fired = event(spec)
        fired["registration_id"] = waiter["delegation"]["registration_id"]
        fired["event_id"] = waiter["event_id"]
        replay = subprocess.run(
            argv, input=json.dumps(fired), text=True, capture_output=True,
            timeout=30, check=False, **no_window_kwargs(),
        )
        assert replay.returncode == 0, replay.stderr
        assert len(queue.list_run_waiter_wakes(task_id)) == 1
        assert read_subscriptions_state() == []


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
