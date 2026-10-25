"""Real admission, accepted sockets and native leases survive failed retirement."""

import json
import threading
import time
from types import SimpleNamespace

import pytest
from agent_procutil import _daemon_instance_store as store
from single_instance_lease import AlreadyRunningError, SingleInstance
from work_coalescing_singleton import CoalescingServer
from work_coalescing_singleton.client import request

from agent_pull_requests import __main__ as cli
from agent_pull_requests import watch_daemon, watch_entry
from agent_pull_requests.watch_contract import PRSnapshot
from agent_pull_requests.watch_writer import WatchWriter


def wait_for(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "native proof timed out"
        time.sleep(0.01)


@pytest.mark.parametrize("passive", [False, True], ids=["legacy", "passive"])
@pytest.mark.parametrize("failure", ["interrupt", "startup", "cleanup", "normal"])
def test_accepted_handler_retains_original_record_and_lease(
    tmp_path, monkeypatch, passive, failure,
):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    servers, clients, owners, errors, responses, receipts = [], [], [], [], [], []
    registry = tmp_path / "instances"
    monkeypatch.delenv("COPILOT_EXTENSIONS_DAEMON_INSTANCE", raising=False)
    monkeypatch.setenv("COPILOT_EXTENSIONS_DAEMON_REGISTRY", str(registry))
    monkeypatch.setattr(store, "_admitted", None)
    monkeypatch.setattr(cli, "_WATCH_INVOCATION", None)
    monkeypatch.setattr(cli, "_WATCH_HANDLER_DRAIN_TIMEOUT_S", 0.02)

    def server_factory(compute, **kwargs):
        def blocked(kind, payload):
            entered.set()
            assert release.wait(15)
            return compute(kind, payload)

        server = CoalescingServer(blocked, **kwargs)
        start = server.start

        def start_and_admit():
            receipts.append(store._admitted[1])
            start()
            endpoint = server.rendezvous()
            host, port = endpoint["endpoint"].rsplit(":", 1)

            def client():
                try:
                    responses.append(request(
                        host, int(port), endpoint["token"],
                        kind="health", key="original", payload={},
                        request_deadline_s=15, completion_timeout_s=15,
                    ))
                except BaseException as exc:
                    errors.append(exc)

            thread = threading.Thread(target=client)
            clients.append(thread)
            thread.start()
            assert entered.wait(3)
            if failure == "startup":
                raise OSError("original startup failure")

        server.start = start_and_admit
        if failure == "cleanup":
            close = server.close_admission
            attempts = []

            def close_admission(**kwargs):
                attempts.append(True)
                if len(attempts) < 3:
                    raise OSError("original intake close failure")
                close(**kwargs)
            server.close_admission = close_admission
        servers.append(server)
        return server

    monkeypatch.setattr("work_coalescing_singleton.CoalescingServer", server_factory)
    monkeypatch.setattr(watch_entry, "WatchControlServer", server_factory)

    def interrupted():
        if failure == "interrupt":
            raise KeyboardInterrupt("original interrupt")

    if passive:
        writer = WatchWriter(
            root=tmp_path / "writer", authorize=lambda _: None,
            fetch=lambda *args: PRSnapshot(pr_state="open"),
        )
        writer._lease.acquire()
        owners.append(writer)
        monkeypatch.setattr(watch_entry, "WatchControlOwner", lambda *a, **kw: SimpleNamespace(
            writer=writer, compute=writer.compute, publish_launch=lambda _: None,
            wait_for_shutdown=interrupted,
        ))
    else:
        original = watch_daemon.WatchDaemon

        def daemon_factory(**kwargs):
            daemon = original(**kwargs)
            daemon.wait_for_shutdown = interrupted
            owners.append(daemon)
            return daemon

        monkeypatch.setattr(watch_daemon, "WatchDaemon", daemon_factory)

    def invoke():
        try:
            args = SimpleNamespace(passive_instance="reserved" if passive else None,
                                   port=1 if passive else 0, poll_interval=30)
            if passive:
                # The endpoint remains a real ephemeral socket, not the reserved port.
                args.port = 1
                watch_entry.serve_passive(
                    SimpleNamespace(runtime=SimpleNamespace(runtime_root=tmp_path)),
                    args, fetch=lambda *args: PRSnapshot(pr_state="open"),
                )
            else:
                cli._cmd_serve(args)
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()

    # Leave binding to the actual native server while avoiding a privileged test port.
    if passive:
        def passive_server(compute, **kwargs):
            kwargs["bind_port"] = 0
            return server_factory(compute, **kwargs)
        monkeypatch.setattr(watch_entry, "WatchControlServer", passive_server)
    entry = threading.Thread(target=invoke)
    entry.start()
    try:
        assert entered.wait(5)
        time.sleep(5.3 if passive else 0.2)
        records = [receipts[0].record_path]
        record = store._read(records[0])
        root = tmp_path / "writer" if passive else tmp_path
        contender = SingleInstance(root, service="agent-pull-requests-watch")
        with pytest.raises(AlreadyRunningError):
            contender.acquire()
        print(json.dumps({
            "path": "passive" if passive else "legacy",
            "handler_native": servers[0].active_handler_count(),
            "record_state": record["state"], "entry_finished": finished.is_set(),
            "original_lease_held": True,
        }))
        assert servers[0].active_handler_count() == 1
        assert record["state"] == "running"
        assert not finished.is_set()
    finally:
        release.set()
        entry.join(10)
        for client in clients:
            client.join(5)
        for server in servers:
            server.close()
        for owner in owners:
            if isinstance(owner, WatchWriter):
                owner.close(timeout=5)
            else:
                owner.close()
    assert finished.is_set()
    assert responses and responses[0].get("pid")
    assert store._read(records[0])["state"] == "finished"
    assert not entry.is_alive()
    if failure == "startup":
        assert isinstance(errors[0], OSError) and str(errors[0]) == "original startup failure"
    elif failure == "cleanup":
        assert isinstance(errors[0], OSError) and str(errors[0]) == "original intake close failure"
    elif passive and failure == "interrupt":
        assert isinstance(errors[0], KeyboardInterrupt)
    else:
        assert isinstance(errors[0], TimeoutError)


@pytest.mark.parametrize("fail_intake", [False, True])
def test_zero_handlers_need_successful_intake_close(tmp_path, monkeypatch, fail_intake):
    from agent_procutil.daemon_instances import daemon_entrypoint
    from agent_pull_requests.watch_retirement import WatchRetirement

    writer = WatchWriter(
        root=tmp_path / "writer", authorize=lambda _: None,
        fetch=lambda *args: PRSnapshot(pr_state="open"),
    )
    writer._lease.acquire()
    server = CoalescingServer(writer.compute, on_idle=None)
    server.start()
    attempted, allow = threading.Event(), threading.Event()
    close = server.close_admission

    def intake(**kwargs):
        attempted.set()
        if fail_intake and not allow.is_set():
            raise OSError("unknown intake")
        close(**kwargs)

    server.close_admission = intake
    errors, receipts = [], []

    @daemon_entrypoint("agent-pull-requests", source=__file__, purpose="production")
    def entry():
        receipts.append(store._admitted[1])
        with WatchRetirement(close_owner=lambda: writer.close(timeout=0.1)) as retirement:
            retirement.server = server

    def invoke():
        try:
            entry()
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=invoke)
    thread.start()
    try:
        assert attempted.wait(3)
        if fail_intake:
            time.sleep(0.1)
            assert server.active_handler_count() == 0
            assert store._read(receipts[0].record_path)["state"] == "running"
            assert writer.writer_held and thread in threading.enumerate()
    finally:
        allow.set()
        thread.join(5)
    assert thread not in threading.enumerate()
    assert store._read(receipts[0].record_path)["state"] == "finished"
    assert not writer.writer_held
    assert len(errors) == int(fail_intake)
    if errors:
        assert str(errors[0]) == "unknown intake"


def test_reattach_failure_keeps_original_lease_until_started_poller_exits(tmp_path, monkeypatch):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    errors, receipts, workers = [], [], []

    def fetch(*args):
        entered.set()
        assert release.wait(10)
        return PRSnapshot(pr_state="open")

    def reattach(daemon):
        receipts.append(store._admitted[1])
        daemon.compute("register", {
            "repo": "sample/repo", "number": 1, "subscriber_id": "original",
            "until": ["merged"], "notify": {},
        })
        assert entered.wait(3)
        workers.extend(daemon._pollers.values())
        raise OSError("original reattach failure")

    monkeypatch.setattr(watch_daemon.WatchDaemon, "_reattach_from_disk", reattach)
    monkeypatch.setattr(cli, "_watch_github_snapshot", fetch)
    monkeypatch.setattr(cli, "_WATCH_INVOCATION", None)

    def invoke():
        try:
            cli._cmd_serve(SimpleNamespace(passive_instance=None, port=0, poll_interval=0.01))
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()

    entry = threading.Thread(target=invoke)
    entry.start()
    try:
        assert entered.wait(3)
        wait_for(lambda: bool(workers))
        assert workers[0] in threading.enumerate()
        assert store._read(receipts[0].record_path)["state"] == "running"
        contender = SingleInstance(tmp_path, service="agent-pull-requests-watch")
        with pytest.raises(AlreadyRunningError):
            contender.acquire()
        assert not finished.is_set()
    finally:
        release.set()
        entry.join(10)
    assert finished.is_set() and workers[0] not in threading.enumerate()
    assert store._read(receipts[0].record_path)["state"] == "finished"
    assert len(errors) == 1 and str(errors[0]) == "original reattach failure"


@pytest.mark.parametrize("delivery", [False, True], ids=["poller", "delivery"])
def test_native_worker_tail_outlives_registry_and_false_join_bookkeeping(
    tmp_path, monkeypatch, delivery,
):
    from agent_pull_requests.watch_delivery import Deliveries

    tail, release = threading.Event(), threading.Event()
    workers = []
    owner = Deliveries if delivery else watch_daemon.WatchDaemon
    name = "_run_legacy" if delivery else "_poll_loop"
    original = getattr(owner, name)

    def retained(self, *args):
        try:
            return original(self, *args)
        finally:
            workers.append(threading.current_thread())
            tail.set()
            assert release.wait(10)

    monkeypatch.setattr(owner, name, retained)
    writer = WatchWriter(
        root=tmp_path / "writer", authorize=lambda _: None,
        fetch=lambda *args: PRSnapshot(pr_state="closed", merged=True),
        notify=lambda _: 0,
    )
    writer._lease.acquire()
    daemon = writer.daemon
    daemon.reload_state(timeout=1)
    daemon.resume_effects(timeout=1)
    daemon.compute("register", {
        "repo": "sample/repo", "number": 1, "subscriber_id": "original",
        "until": ["merged"], "notify": {},
    })
    if not delivery:
        daemon._shutdown_event.set()
    try:
        assert tail.wait(3)
        thread = workers[0]
        if delivery:
            assert not daemon._deliveries.workers
        else:
            assert not daemon._pollers
        # Reproduce CPython's interrupted-join bookkeeping without terminating
        # the actual native worker, whose completion remains gated above.
        if hasattr(thread, "_tstate_lock"):
            monkeypatch.setattr(thread, "_is_stopped", True)
            monkeypatch.setattr(thread, "_tstate_lock", None)
            assert not thread.is_alive()
        assert thread in threading.enumerate()
        with pytest.raises(TimeoutError):
            writer.close(timeout=0.03)
        assert writer.writer_held and thread in threading.enumerate()
    finally:
        release.set()
        wait_for(lambda: all(worker not in threading.enumerate() for worker in workers))
        writer.close(timeout=1)
    assert not writer.writer_held


def test_finished_handler_count_is_not_native_handler_completion(tmp_path):
    from agent_procutil.daemon_instances import daemon_entrypoint
    from agent_pull_requests.watch_retirement import WatchRetirement

    tail, release, ready, shutdown = (
        threading.Event(), threading.Event(), threading.Event(), threading.Event(),
    )
    writer = WatchWriter(
        root=tmp_path / "writer", authorize=lambda _: None,
        fetch=lambda *args: PRSnapshot(pr_state="open"),
    )
    writer._lease.acquire()
    server = CoalescingServer(writer.compute, on_idle=None)
    process = server._server.process_request_thread
    receipts, handlers = [], []

    def native_tail(*args):
        process(*args)
        handlers.append(threading.current_thread())
        tail.set()
        assert release.wait(10)

    server._server.process_request_thread = native_tail

    @daemon_entrypoint("agent-pull-requests", source=__file__, purpose="production")
    def entry():
        receipts.append(store._admitted[1])
        with WatchRetirement(close_owner=lambda: writer.close(timeout=1)) as retirement:
            retirement.server = server
            server.start()
            ready.set()
            assert shutdown.wait(10)

    thread = threading.Thread(target=entry)
    thread.start()
    try:
        assert ready.wait(3)
        endpoint = server.rendezvous()
        host, port = endpoint["endpoint"].rsplit(":", 1)
        assert request(host, int(port), endpoint["token"],
                       kind="health", key="original", payload={},
                       request_deadline_s=3)["pid"]
        assert tail.wait(3)
        shutdown.set()
        time.sleep(0.15)
        assert server.active_handler_count() == 0
        assert handlers[0] in threading.enumerate()
        assert writer.writer_held and thread in threading.enumerate()
        assert store._read(receipts[0].record_path)["state"] == "running"
    finally:
        shutdown.set()
        release.set()
        thread.join(5)
    assert thread not in threading.enumerate()
    assert handlers[0] not in threading.enumerate()
    assert not writer.writer_held
    assert store._read(receipts[0].record_path)["state"] == "finished"
