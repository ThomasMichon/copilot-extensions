"""Tests for the coordinator's rendezvous-file advertising and dynamic bind.

Phase 3 Stage A advertised the bound endpoint; Stage C flips the bind to an
OS-assigned ephemeral port (``127.0.0.1:0``) unless ``AGENT_DISPATCH_PORT`` pins
one, and advertises the *actual* port read back off the socket.
"""

from __future__ import annotations

import socket

import uvicorn

from agent_dispatch import rendezvous, server
from agent_dispatch.config import Config


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def test_advertise_endpoint_writes_rendezvous(monkeypatch, tmp_path):
    run = tmp_path / "run"
    monkeypatch.setenv("AGENT_DISPATCH_RUN_DIR", str(run))
    path = server.advertise_endpoint(Config(host="127.0.0.1", port=9847))
    assert path is not None
    ep = rendezvous.read_endpoint(run)
    assert ep is not None
    assert ep.transport == "tcp"
    assert ep.tcp_host_port == ("127.0.0.1", 9847)


def test_advertise_endpoint_reflects_bound_host(monkeypatch, tmp_path):
    run = tmp_path / "run"
    monkeypatch.setenv("AGENT_DISPATCH_RUN_DIR", str(run))
    # NAT bind host (vEthernet IP) + a dynamic port are advertised verbatim.
    server.advertise_endpoint(Config(host="172.19.240.1", port=52731))
    ep = rendezvous.read_endpoint(run)
    assert ep is not None
    assert ep.tcp_host_port == ("172.19.240.1", 52731)


def test_clear_endpoint_removes_owned_record(tmp_path):
    run = tmp_path / "run"
    rendezvous.write_endpoint(run, "tcp", "127.0.0.1:41001", pid=1001)

    rendezvous.clear_endpoint(run, owner_pid=1001)

    assert rendezvous.read_endpoint(run) is None


def test_clear_endpoint_preserves_successor_owned_record(tmp_path):
    run = tmp_path / "run"
    rendezvous.write_endpoint(run, "tcp", "127.0.0.1:41002", pid=2002)

    rendezvous.clear_endpoint(run, owner_pid=1001)

    ep = rendezvous.read_endpoint(run)
    assert ep is not None
    assert ep.pid == 2002
    assert ep.tcp_host_port == ("127.0.0.1", 41002)


def test_clear_endpoint_cutover_retains_newest_successor_record(tmp_path):
    run = tmp_path / "run"
    rendezvous.write_endpoint(run, "tcp", "127.0.0.1:41001", pid=1001)
    rendezvous.write_endpoint(run, "tcp", "127.0.0.1:41002", pid=2002)

    rendezvous.clear_endpoint(run, owner_pid=1001)

    ep = rendezvous.read_endpoint(run)
    assert ep is not None
    assert ep.pid == 2002
    assert ep.tcp_host_port == ("127.0.0.1", 41002)


def test_clear_endpoint_removes_non_utf8_record(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    rendezvous.endpoint_file(run).write_bytes(b"\xff")

    rendezvous.clear_endpoint(run, owner_pid=1001)

    assert not rendezvous.endpoint_file(run).exists()


def test_clear_endpoint_rechecks_owner_before_unlink(monkeypatch, tmp_path):
    run = tmp_path / "run"
    rendezvous.write_endpoint(run, "tcp", "127.0.0.1:41001", pid=1001)
    owner = rendezvous.read_endpoint(run)
    successor = rendezvous.Endpoint("tcp", "127.0.0.1:41002", pid=2002)
    records = iter((owner, successor))
    monkeypatch.setattr(rendezvous, "read_endpoint", lambda _runtime_dir: next(records))

    rendezvous.clear_endpoint(run, owner_pid=1001)

    assert rendezvous.endpoint_file(run).exists()


def test_server_bind_port_defaults_to_zero(monkeypatch):
    monkeypatch.delenv("AGENT_DISPATCH_PORT", raising=False)
    assert server._server_bind_port() == 0


def test_server_bind_port_honors_pin(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_PORT", "9847")
    assert server._server_bind_port() == 9847


def test_server_bind_port_ignores_garbage(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_PORT", "not-a-port")
    assert server._server_bind_port() == 0


def test_bind_listen_socket_assigns_os_port():
    sock = server._bind_listen_socket("127.0.0.1", 0)
    try:
        host, port = sock.getsockname()[:2]
        assert host == "127.0.0.1"
        assert port != 0  # OS assigned a real ephemeral port
    finally:
        sock.close()


def _run_serve_capturing(monkeypatch, run, cfg):
    """Run ``serve`` with uvicorn stubbed out, returning the endpoint advertised
    while the server was 'running' plus the sockets uvicorn was handed."""
    # Isolate the routing-table lookup too, not just run_dir() -- otherwise
    # the non-passive liveness guard consults *this real host's* actual
    # routing table and can spuriously refuse to start under `serve(cfg)`
    # whenever a real coordinator happens to be live on the machine running
    # the tests.
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(run.parent / "routing"))
    seen: dict = {}

    def _fake_run(self, sockets=None):
        seen["during"] = rendezvous.read_endpoint(run)
        seen["sockets"] = sockets
        seen["bound_port"] = sockets[0].getsockname()[1] if sockets else None

    monkeypatch.setattr(uvicorn.Server, "run", _fake_run)
    server.serve(cfg)
    return seen


def test_serve_binds_os_assigned_port_and_advertises_it(monkeypatch, tmp_path):
    run = tmp_path / "run"
    monkeypatch.setenv("AGENT_DISPATCH_RUN_DIR", str(run))
    monkeypatch.delenv("AGENT_DISPATCH_PORT", raising=False)
    cfg = Config(host="127.0.0.1", port=9847, db_path=str(tmp_path / "tasks.db"))

    seen = _run_serve_capturing(monkeypatch, run, cfg)

    # A real OS-assigned port was bound -- not the legacy fixed 9847 -- and the
    # rendezvous file advertises exactly that bound port while serving.
    assert seen["during"] is not None
    advertised_host, advertised_port = seen["during"].tcp_host_port
    assert advertised_host == "127.0.0.1"
    assert advertised_port == seen["bound_port"]
    assert advertised_port != 9847
    # Cleared on shutdown.
    assert rendezvous.read_endpoint(run) is None


def test_serve_honors_pinned_port(monkeypatch, tmp_path):
    run = tmp_path / "run"
    monkeypatch.setenv("AGENT_DISPATCH_RUN_DIR", str(run))
    pinned = _free_port()
    monkeypatch.setenv("AGENT_DISPATCH_PORT", str(pinned))
    cfg = Config(host="127.0.0.1", port=pinned, db_path=str(tmp_path / "tasks.db"))

    seen = _run_serve_capturing(monkeypatch, run, cfg)

    assert seen["bound_port"] == pinned
    assert seen["during"].tcp_host_port == ("127.0.0.1", pinned)
    assert rendezvous.read_endpoint(run) is None


# -- non-passive serve() refuses to seize an already-live route (#3066) -----


def test_serve_raises_when_coordinator_already_live(monkeypatch, tmp_path):
    import pytest

    run = tmp_path / "run"
    monkeypatch.setenv("AGENT_DISPATCH_RUN_DIR", str(run))
    monkeypatch.setattr(server, "has_live_local_coordinator", lambda **_: True)
    cfg = Config(host="127.0.0.1", port=0, db_path=str(tmp_path / "tasks.db"))

    with pytest.raises(server.CoordinatorAlreadyLiveError):
        server.serve(cfg)

    # Refused before ever binding/advertising/publishing anything.
    assert rendezvous.read_endpoint(run) is None


def test_serve_releases_start_lock_when_liveness_recheck_raises(monkeypatch, tmp_path):
    """An exception from the liveness re-check itself (e.g. a malformed
    AGENT_DISPATCH_ENDPOINT override) must not strand the start lock -- a
    caller that catches it and retries in the same process would otherwise
    deadlock against its own held lock (review follow-up on
    ThomasMichon/copilot-extensions#3066)."""
    import pytest
    from agent_dispatch.single_instance import SingleInstance

    run = tmp_path / "run"
    routing = tmp_path / "routing"
    monkeypatch.setenv("AGENT_DISPATCH_RUN_DIR", str(run))
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(routing))

    def _boom(**_kwargs):
        raise ValueError("malformed AGENT_DISPATCH_ENDPOINT override")

    monkeypatch.setattr(server, "has_live_local_coordinator", _boom)
    cfg = Config(host="127.0.0.1", port=0, db_path=str(tmp_path / "tasks.db"))

    with pytest.raises(ValueError):
        server.serve(cfg)

    # The lock must be free for a same-process retry.
    lock_path = routing / "serve-start.lock"
    probe = SingleInstance(lock_path)
    assert probe.acquire(), "start lock was left held after the liveness re-check raised"
    probe.release()


def test_loopback_probe_url_brackets_ipv6_hosts():
    assert server._loopback_probe_url("127.0.0.1", 1234) == "http://127.0.0.1:1234"
    assert server._loopback_probe_url("::1", 1234) == "http://[::1]:1234"
    assert server._loopback_probe_url("::", 1234) == "http://[::1]:1234"
    # Already-bracketed input is passed through rather than double-wrapped.
    assert server._loopback_probe_url("[::1]", 1234) == "http://[::1]:1234"


def test_loopback_probe_url_normalizes_wildcard_binds():
    # check_bind_safety() explicitly permits a wildcard/unspecified bind when
    # a token is configured, but it isn't a dialable loopback destination.
    assert server._loopback_probe_url("0.0.0.0", 1234) == "http://127.0.0.1:1234"
    assert server._loopback_probe_url("::", 1234) == "http://[::1]:1234"
    assert server._loopback_probe_url("[::]", 1234) == "http://[::1]:1234"


def test_publish_routing_normalizes_bracketed_ipv6_wildcard(monkeypatch, tmp_path):
    # zdd.routing.Endpoint.client_host only special-cases the unbracketed
    # "::" wildcard, not "[::]" -- WILDCARD_BIND_HOSTS explicitly permits
    # both as a configured Config.host, so a bracketed bind must be
    # normalized before it reaches the routing table, or it round-trips
    # unnormalized and produces an unroutable "http://[::]:<port>" client
    # URL, misclassifying a healthy wildcard-bound coordinator as dead
    # (review follow-up on ThomasMichon/copilot-extensions#3066).
    import zdd.routing as zdd_routing

    run = tmp_path / "run"
    routing = tmp_path / "routing"
    monkeypatch.setenv("AGENT_DISPATCH_RUN_DIR", str(run))
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(routing))
    monkeypatch.setattr(zdd_routing, "reap_stale_active", lambda *a, **k: None)

    captured: dict = {}

    def _fake_publish_active(_routing_dir, *, bind, **kwargs):
        captured["bind"] = bind

    monkeypatch.setattr(zdd_routing, "publish_active", _fake_publish_active)

    cfg = Config(host="[::]", port=0, db_path=str(tmp_path / "tasks.db"))
    server._publish_routing(cfg, 1234)

    assert captured["bind"] == "::"


def test_serve_holds_start_lock_until_actually_responsive(monkeypatch, tmp_path):
    """The start lock must stay held past the route publish, through
    uvicorn's own startup, until this instance is verifiably answering its
    own ``/health`` -- otherwise a concurrent starter racing in during that
    gap would see the brand-new, not-yet-serving route as "not live" and
    seize it right back out (review follow-up on
    ThomasMichon/copilot-extensions#3066)."""
    import threading as _threading
    import time as _time

    from agent_dispatch.single_instance import SingleInstance

    run = tmp_path / "run"
    routing = tmp_path / "routing"
    monkeypatch.setenv("AGENT_DISPATCH_RUN_DIR", str(run))
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(routing))
    monkeypatch.setattr(server, "has_live_local_coordinator", lambda **_: False)

    release_run = _threading.Event()
    ready = _threading.Event()

    def _fake_run(self, sockets=None):
        # Simulate uvicorn taking a moment to actually start serving.
        release_run.wait(timeout=5.0)

    monkeypatch.setattr(uvicorn.Server, "run", _fake_run)

    became_responsive_calls: list[bool] = []

    def _fake_self_health_responsive(url, *, timeout=None, token=None):
        # First few polls: not yet responsive (simulating uvicorn still
        # starting up). Once we decide to let it "become ready", report True
        # and let the real serve() call proceed to release the lock.
        result = ready.is_set()
        became_responsive_calls.append(result)
        if result:
            release_run.set()
        return result

    monkeypatch.setattr(
        "agent_dispatch.config._health_responsive", _fake_self_health_responsive
    )

    cfg = Config(host="127.0.0.1", port=0, db_path=str(tmp_path / "tasks.db"))
    result: dict = {}

    def _run_serve():
        try:
            server.serve(cfg)
        except BaseException as exc:  # noqa: BLE001 -- surfaced to the test thread
            result["error"] = exc

    t = _threading.Thread(target=_run_serve, daemon=True)
    t.start()
    try:
        # Give serve() time to acquire the lock and reach the readiness poll.
        # Watch the unlocked ".owner" side-car rather than repeatedly
        # acquiring/releasing the real lock from this thread -- doing that in
        # a loop creates its own narrow TOCTOU race against serve()'s first
        # acquire attempt (this thread could be mid-acquire, however briefly,
        # exactly when serve() tries and spuriously fails).
        deadline = _time.monotonic() + 5.0
        lock_path = routing / "serve-start.lock"
        owner_path = lock_path.with_suffix(lock_path.suffix + ".owner")
        while _time.monotonic() < deadline and not owner_path.exists():
            _time.sleep(0.02)
        assert owner_path.exists(), "serve() never appeared to acquire the start lock"
        # Now confirm a concurrent starter cannot acquire it while this
        # instance isn't yet responsive (a single attempt -- no need to loop).
        contender = SingleInstance(lock_path)
        assert not contender.acquire(), (
            "a concurrent starter must not acquire the lock before this "
            "instance is confirmed responsive"
        )
        # Now let it "become ready" -- the lock must be released promptly.
        ready.set()
        deadline = _time.monotonic() + 5.0
        while _time.monotonic() < deadline and owner_path.exists():
            _time.sleep(0.02)
        assert not owner_path.exists(), (
            "start lock was never released after becoming responsive"
        )
    finally:
        release_run.set()
        ready.set()
        t.join(timeout=5.0)
    assert "error" not in result
    assert any(became_responsive_calls), "readiness probe was never consulted"


def test_serve_releases_lock_even_if_poller_join_times_out(monkeypatch, tmp_path):
    """The bounded ``poller.join()`` in ``serve()``'s finally block is a
    best-effort wait, not a guarantee the readiness poller has actually
    released the lock -- a slow DNS/HTTP probe can still be mid-flight past
    that timeout. ``serve()`` must release the lock itself as a fallback
    (a safe no-op if the poller already did it), or a stuck probe could
    strand ``serve-start.lock`` until process exit and block every later
    start/cutover (review follow-up on ThomasMichon/copilot-extensions#3066).
    """
    import time as _time

    from agent_dispatch.single_instance import SingleInstance

    run = tmp_path / "run"
    routing = tmp_path / "routing"
    monkeypatch.setenv("AGENT_DISPATCH_RUN_DIR", str(run))
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(routing))
    monkeypatch.setattr(server, "has_live_local_coordinator", lambda **_: False)

    def _fake_run(self, sockets=None):
        pass  # returns immediately, well before the readiness poller does

    monkeypatch.setattr(uvicorn.Server, "run", _fake_run)

    def _slow_health_responsive(url, *, timeout=None, token=None):
        # Simulate a probe call that outlives serve()'s own bounded
        # poller.join() window.
        _time.sleep(3.0)
        return True

    monkeypatch.setattr(
        "agent_dispatch.config._health_responsive", _slow_health_responsive
    )

    cfg = Config(host="127.0.0.1", port=0, db_path=str(tmp_path / "tasks.db"))
    server.serve(cfg)

    # The core assertion: even though the poller's own health probe outlived
    # serve()'s bounded join, the lock must still end up released (the
    # fallback release in the finally block), not stranded until process
    # exit.
    probe = SingleInstance(routing / "serve-start.lock")
    assert probe.acquire(), (
        "start lock must be released even after a slow probe outlives the "
        "poller's bounded join"
    )
    probe.release()


def test_serve_raises_when_start_lock_already_held(monkeypatch, tmp_path):
    """A second, concurrent non-passive serve() must not slip past the
    friendly caller-side pre-check and seize the route -- the lock itself is
    the atomic guard (ThomasMichon/copilot-extensions#3066 follow-up)."""
    import pytest

    from agent_dispatch.single_instance import SingleInstance

    run = tmp_path / "run"
    routing = tmp_path / "routing"
    monkeypatch.setenv("AGENT_DISPATCH_RUN_DIR", str(run))
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(routing))
    monkeypatch.setattr(server, "has_live_local_coordinator", lambda **_: False)
    # Simulate a concurrent starter already holding the lock. The lock is
    # keyed by routing_dir() (the shared, raced-over resource), not run_dir().
    routing.mkdir(parents=True, exist_ok=True)
    holder = SingleInstance(routing / "serve-start.lock")
    assert holder.acquire()
    try:
        cfg = Config(host="127.0.0.1", port=0, db_path=str(tmp_path / "tasks.db"))
        with pytest.raises(server.CoordinatorAlreadyLiveError):
            server.serve(cfg)
        assert rendezvous.read_endpoint(run) is None
    finally:
        holder.release()


def test_serve_raises_when_start_lock_already_held_includes_holder_pid(
    monkeypatch, tmp_path
):
    """The refusal message must surface the recorded holder pid -- a
    genuinely wedged (not just slow) holder can only be recovered by
    terminating it externally, and an operator or watchdog needs the pid to
    act on that (review follow-up on ThomasMichon/copilot-extensions#3066)."""
    import os as os_mod

    import pytest

    from agent_dispatch.single_instance import SingleInstance

    run = tmp_path / "run"
    routing = tmp_path / "routing"
    monkeypatch.setenv("AGENT_DISPATCH_RUN_DIR", str(run))
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(routing))
    monkeypatch.setattr(server, "has_live_local_coordinator", lambda **_: False)
    routing.mkdir(parents=True, exist_ok=True)
    holder = SingleInstance(routing / "serve-start.lock")
    assert holder.acquire()
    try:
        cfg = Config(host="127.0.0.1", port=0, db_path=str(tmp_path / "tasks.db"))
        with pytest.raises(server.CoordinatorAlreadyLiveError) as exc_info:
            server.serve(cfg)
        assert str(os_mod.getpid()) in str(exc_info.value)
    finally:
        holder.release()


def test_serve_readiness_poller_survives_unexpected_probe_exception(
    monkeypatch, tmp_path
):
    """An unexpected exception from the readiness probe (``_health_responsive``
    normally narrows expected failures to ``False``, but this guards the
    unexpected case too) must not kill the poller thread -- otherwise
    ``server.run()`` keeps blocking while the lock is never released, failing
    every later serve/deploy attempt (review follow-up on
    ThomasMichon/copilot-extensions#3066)."""
    import threading as _threading

    from agent_dispatch.single_instance import SingleInstance

    run = tmp_path / "run"
    routing = tmp_path / "routing"
    monkeypatch.setenv("AGENT_DISPATCH_RUN_DIR", str(run))
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(routing))
    monkeypatch.setattr(server, "has_live_local_coordinator", lambda **_: False)

    # Keep server.run() "running" (as uvicorn would while actually serving)
    # until the test lets it finish, so _server_exited doesn't short-circuit
    # the poller after just one exception.
    release_run = _threading.Event()

    def _fake_run(self, sockets=None):
        release_run.wait(timeout=5.0)

    monkeypatch.setattr(uvicorn.Server, "run", _fake_run)

    calls: list[int] = []

    def _flaky_health_responsive(url, *, timeout=None, token=None):
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("unexpected probe failure")
        release_run.set()
        return True

    monkeypatch.setattr(
        "agent_dispatch.config._health_responsive", _flaky_health_responsive
    )

    cfg = Config(host="127.0.0.1", port=0, db_path=str(tmp_path / "tasks.db"))
    server.serve(cfg)  # must not raise, and must not hang

    assert len(calls) >= 3
    probe = SingleInstance(routing / "serve-start.lock")
    assert probe.acquire(), "lock must be released once the probe recovers"
    probe.release()


def test_serve_force_bypasses_live_coordinator_guard(monkeypatch, tmp_path):
    run = tmp_path / "run"
    # Isolate routing_dir()/install_dir() too -- force=True skips the guard
    # and reaches _publish_routing()/write_running_version(), which would
    # otherwise write this test's PID/port into the real user's routing
    # table and install manifest.
    monkeypatch.setenv("AGENT_DISPATCH_RUN_DIR", str(run))
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(tmp_path / "routing"))
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path / "install"))
    monkeypatch.setattr(server, "has_live_local_coordinator", lambda **_: True)

    def _fake_run(self, sockets=None):
        pass

    monkeypatch.setattr(uvicorn.Server, "run", _fake_run)
    cfg = Config(host="127.0.0.1", port=0, db_path=str(tmp_path / "tasks.db"))

    # Must not raise despite a live coordinator, and must actually publish.
    server.serve(cfg, force=True)
    assert rendezvous.read_endpoint(run) is None  # cleared on shutdown, as usual


def test_serve_force_still_serializes_against_concurrent_transition(monkeypatch, tmp_path):
    """``force`` bypasses only the live-coordinator *rejection*, not the
    routing-transition lock itself -- otherwise a forced start could still
    race a normal ``serve()`` or an in-progress `deploy`/cutover (which holds
    this same lock through its own transition) and publish over it,
    recreating the exact undrained duplicate this guard exists to prevent
    (review follow-up on ThomasMichon/copilot-extensions#3066)."""
    import pytest

    from agent_dispatch.single_instance import SingleInstance

    run = tmp_path / "run"
    routing = tmp_path / "routing"
    monkeypatch.setenv("AGENT_DISPATCH_RUN_DIR", str(run))
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(routing))
    # has_live_local_coordinator must never even be consulted when force=True
    # (its rejection is bypassed entirely) -- only the lock acquisition
    # itself should be able to refuse the start.
    monkeypatch.setattr(
        server,
        "has_live_local_coordinator",
        lambda **_: (_ for _ in ()).throw(
            AssertionError("force=True must not consult liveness")
        ),
    )
    # Simulate a concurrent starter (or an in-progress deploy/cutover)
    # already holding the transition lock.
    routing.mkdir(parents=True, exist_ok=True)
    holder = SingleInstance(routing / "serve-start.lock")
    assert holder.acquire()
    try:
        cfg = Config(host="127.0.0.1", port=0, db_path=str(tmp_path / "tasks.db"))
        with pytest.raises(server.CoordinatorAlreadyLiveError):
            server.serve(cfg, force=True)
        assert rendezvous.read_endpoint(run) is None
    finally:
        holder.release()


def test_serve_passive_never_checks_live_coordinator(monkeypatch, tmp_path):
    run = tmp_path / "run"
    monkeypatch.setenv("AGENT_DISPATCH_RUN_DIR", str(run))

    def _boom():
        raise AssertionError("passive serve() must never consult liveness")

    monkeypatch.setattr(server, "has_live_local_coordinator", _boom)

    def _fake_run(self, sockets=None):
        pass

    monkeypatch.setattr(uvicorn.Server, "run", _fake_run)
    cfg = Config(host="127.0.0.1", port=0, db_path=str(tmp_path / "tasks.db"))

    server.serve(cfg, passive=True)
