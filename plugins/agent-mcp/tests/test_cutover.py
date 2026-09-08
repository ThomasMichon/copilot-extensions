"""Tests for zero-downtime `serve` cutover (agent_mcp.cutover, and the
Server-side control-plane additions in agent_mcp.serve).

Covers the unit-level pieces directly (control op dispatch, lease bypass for
--passive, the token-path pid-keying that fixes the cross-attempt token
collision, flip_data_handle's POSIX symlink swap) plus one full subprocess
end-to-end test that spawns a real "old" daemon, runs a real cutover against
it, and confirms: the old process actually exits, the fixed client-facing
handle now resolves to the new generation, and a plain data-plane client
(unaware anything happened) transparently reaches it.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import time

import pytest

from agent_mcp import cutover as _cutover
from agent_mcp.serve import (
    _HAS_AF_UNIX,
    Server,
    _control_token_path,
    flip_data_handle,
)


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


# ── control op dispatch (unit-level, no sockets) ─────────────────────────


def test_control_dispatch_rejects_wrong_token():
    server = Server("unused.sock", enable_lease=False, control_token="right")
    resp = server._control_dispatch({"op": "ping", "token": "wrong"})
    assert resp == {"ok": False, "error": "unauthorized"}


def test_control_dispatch_ping_reports_state():
    server = Server("unused.sock", enable_lease=False, control_token="t")
    resp = server._control_dispatch({"op": "ping", "token": "t"})
    assert resp["ok"] and resp["pong"] is True
    assert resp["draining"] is False
    assert resp["attached"] == 0


def test_control_dispatch_drain_reports_busy_sessions_key():
    """zdd.cutover reads exactly `busy_sessions` from the drain reply (and
    `drained`/`clean`/`forced` from the *client's* drain() return, a
    different shape) -- a renamed/missing key here silently breaks the real
    orchestrator's busy-oracle wait without any test ever calling it."""
    server = Server("unused.sock", enable_lease=False, control_token="t")
    resp = server._control_dispatch({"op": "drain", "token": "t"})
    assert resp["ok"] is True
    assert resp["busy_sessions"] == 0
    assert server.draining is True


def test_control_dispatch_drain_reflects_attached_count():
    server = Server("unused.sock", enable_lease=False, control_token="t")
    server._attached = 2
    resp = server._control_dispatch({"op": "drain", "token": "t"})
    assert resp["busy_sessions"] == 2


def test_control_dispatch_undrain_clears_draining():
    server = Server("unused.sock", enable_lease=False, control_token="t")
    server._control_dispatch({"op": "drain", "token": "t"})
    assert server.draining is True
    resp = server._control_dispatch({"op": "undrain", "token": "t"})
    assert resp == {"ok": True}
    assert server.draining is False


def test_control_dispatch_unknown_op():
    server = Server("unused.sock", enable_lease=False, control_token="t")
    resp = server._control_dispatch({"op": "bogus", "token": "t"})
    assert resp["ok"] is False
    assert "unknown control op" in resp["error"]


# ── passive lease bypass ──────────────────────────────────────────────────


def test_passive_server_skips_lease_acquisition():
    """A --passive instance must never contend for the home-wide single-
    instance lease -- it binds its own distinct socket_path, and what makes
    it the daemon real clients reach is the routing flip, not the lease."""
    server = Server("some/other/path.sock", enable_lease=True, passive=True)
    assert server._acquire_lease() is True
    assert server._lease is None  # never even constructed a SingleInstance


@pytest.mark.asyncio
async def test_passive_server_never_self_publishes_to_routing_table(tmp_path):
    """Regression test: a --passive daemon must NOT publish itself as the
    zdd-routing-table's active control endpoint. The orchestrator's own
    CutoverOrchestrator.run() already does that at the flip step, using the
    exact port it chose -- a passive daemon publishing itself first would
    race ahead of health-gating (a crash between self-publish and a failed
    health check leaves the table pointing at a dead daemon) and corrupt the
    demote-to-`previous` bookkeeping (the orchestrator would see the
    passive's own premature entry as "old" and demote that, not the real
    predecessor)."""
    from zdd import routing

    server = Server(str(tmp_path / "serve-g9.sock"), enable_lease=False,
                    passive=True, control_port=0)
    await server._start_control_listener()
    try:
        assert routing.read_active_endpoint(tmp_path, verify_listener=False) is None
    finally:
        await server._stop_control_listener()


@pytest.mark.asyncio
async def test_non_passive_server_self_publishes_to_routing_table(tmp_path):
    """The positive case: a plain (non-passive) daemon DOES self-publish, so
    the very first cutover ever run has something to discover (agent-mcp has
    no static/well-known control port to fall back on)."""
    from zdd import routing

    server = Server(str(tmp_path / "serve.sock"), enable_lease=False,
                    control_port=0, version="9.9.9")
    await server._start_control_listener()
    try:
        active = routing.read_active_endpoint(tmp_path, verify_listener=False)
        assert active is not None
        assert active.version == "9.9.9"
    finally:
        await server._stop_control_listener()


# ── cleanup must never destroy a handle a flip already repointed ─────────



@pytest.mark.skipif(not _HAS_AF_UNIX, reason="POSIX symlink ownership check")
def test_cleanup_endpoint_leaves_a_flipped_symlink_alone(tmp_path):
    """Regression test (caught by CI on the first PR revision, not by any
    local run -- a timing-dependent race): the old daemon's own socket_path
    IS the fixed, client-facing handle when it was never started with
    --socket. If a cutover flips that exact path into a symlink pointing at
    the newly-promoted generation *before* this (correctly retiring) old
    daemon's cleanup runs, an unconditional unlink would destroy the new
    generation's handle moments after the cutover committed. A symlink here
    is never this daemon's own artifact (it only ever binds a plain file/
    real socket), so cleanup must detect one and leave it alone."""
    fixed = tmp_path / "serve.sock"
    other_gen = tmp_path / "serve-g2.sock"
    other_gen.touch()
    flip_data_handle(other_gen, legacy_path=fixed)
    assert fixed.is_symlink()

    server = Server(str(fixed), enable_lease=False)
    server._cleanup_endpoint()

    assert fixed.is_symlink(), "cleanup must not remove a handle it doesn't own"
    assert os.readlink(fixed) == str(other_gen)


@pytest.mark.skipif(not _HAS_AF_UNIX, reason="POSIX symlink ownership check")
def test_cleanup_endpoint_removes_its_own_unflipped_socket(tmp_path):
    """The positive case: a plain (never-flipped) socket file is still this
    daemon's own -- cleanup must still remove it normally."""
    own = tmp_path / "serve.sock"
    real_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    real_sock.bind(str(own))
    real_sock.close()
    assert own.exists() and not own.is_symlink()

    server = Server(str(own), enable_lease=False)
    server._cleanup_endpoint()

    assert not own.exists()


# ── CutoverClient.shutdown() must flip the data handle BEFORE the wire op ──


@pytest.mark.skipif(not _HAS_AF_UNIX, reason="POSIX symlink flip")
@pytest.mark.asyncio
async def test_shutdown_flips_data_handle_before_sending_the_op(tmp_path, monkeypatch):
    """Regression test (Copilot review): zdd's orchestrator requests the old
    daemon's shutdown at the commit point *inside* CutoverOrchestrator.run().
    Flipping the fixed data handle only *after* run() returns leaves a real
    window where the old daemon has already been told to stop while the
    handle still points at it -- undermining the whole zero-downtime intent.
    CutoverClient.shutdown() must flip the handle before it even sends the
    op, so by the time shutdown() returns (regardless of how fast the old
    daemon reacts), the fixed handle already resolves to the new
    generation."""
    # shutdown() always flips ipc.default_socket_path() (the real fixed
    # handle, matching production's run_cutover -- ctx.data_root is always
    # derived from it there); point that at tmp_path for this test.
    monkeypatch.setenv("AGENT_MCP_HOME", str(tmp_path))
    fixed = tmp_path / "serve.sock"
    old_gen = tmp_path / "serve-g1.sock"
    new_gen = tmp_path / "serve-g2.sock"
    old_gen.touch()
    new_gen.touch()
    flip_data_handle(old_gen, legacy_path=fixed)  # simulate the old daemon's own generation

    server = Server(str(fixed), enable_lease=False, control_port=0,
                    control_token="tok")
    await server._start_control_listener()
    try:
        port = server._control_server.sockets[0].getsockname()[1]
        ctx = _cutover._CutoverContext(
            data_root=tmp_path, new_socket_path=new_gen,
            new_log_path=tmp_path / "x.log", new_control_token="tok",
            old_control_token=None, token_by_port={port: "tok"},
        )
        client = _cutover.CutoverClient(f"http://127.0.0.1:{port}", ctx)
        # shutdown() makes a blocking (sync) socket call; run it on a worker
        # thread so this test's own event loop stays free to service the
        # control listener concurrently (a real cutover never has this
        # problem -- client and server are always separate OS processes).
        import asyncio
        await asyncio.to_thread(client.shutdown)

        assert os.readlink(fixed) == str(new_gen), (
            "the fixed handle must already point at the new generation "
            "once shutdown() returns, before the old daemon has necessarily "
            "finished reacting to the shutdown op"
        )
    finally:
        await server._stop_control_listener()


# ── CutoverClient.drain() must not mistake a rejected reply for "drained" ──


@pytest.mark.asyncio
async def test_drain_client_treats_unauthorized_reply_as_not_drained(tmp_path):
    """Regression test: a falsy/missing `busy_sessions` (which an
    unauthorized or malformed reply naturally has, since only a successful
    `{"ok": true, ...}` drain response carries it) must never be read as
    "cleanly drained" -- the orchestrator would otherwise proceed as if the
    old daemon actually drained when it in fact just rejected the request."""
    server = Server(str(tmp_path / "serve.sock"), enable_lease=False,
                    control_port=0, control_token="right-token")
    await server._start_control_listener()
    try:
        port = server._control_server.sockets[0].getsockname()[1]
        ctx = _cutover._CutoverContext(
            data_root=tmp_path, new_socket_path=tmp_path / "x.sock",
            new_log_path=tmp_path / "x.log", new_control_token="wrong-token",
            old_control_token=None, token_by_port={port: "wrong-token"},
        )
        client = _cutover.CutoverClient(f"http://127.0.0.1:{port}", ctx)
        # A blocking (sync) socket call -- run it on a worker thread so this
        # test's own event loop stays free to service the control listener
        # concurrently (a real cutover never has this problem -- client and
        # server are always separate OS processes). Without this, the first
        # request blocks the loop entirely and only "succeeds" via its own
        # 5s socket timeout -- which happens to look like a rejection too,
        # silently testing the wrong thing.
        import asyncio
        result = await asyncio.to_thread(
            client.drain, timeout=0.5, poll=0.1, force=False)
        assert result["drained"] is False
        assert result["clean"] is False
        assert "error" in result
    finally:
        await server._stop_control_listener()


# ── token-path pid keying (the cross-attempt collision fix) ───────────────


def test_control_token_path_is_keyed_by_pid(tmp_path):
    """Regression test: a single shared token filename let a second cutover
    attempt read a stale token left behind by an unrelated (e.g. previously
    rolled-back) generation, silently sending an unauthenticated shutdown
    that the real old daemon rejected -- leaving it running forever with the
    cutover reporting success. Different pids must never collide."""
    p1 = _control_token_path(tmp_path, 111)
    p2 = _control_token_path(tmp_path, 222)
    assert p1 != p2
    p1.write_text("token-for-111", encoding="utf-8")
    p2.write_text("token-for-222", encoding="utf-8")
    assert p1.read_text(encoding="utf-8") == "token-for-111"
    assert p2.read_text(encoding="utf-8") == "token-for-222"


# ── flip_data_handle ───────────────────────────────────────────────────────


@pytest.mark.skipif(not _HAS_AF_UNIX, reason="POSIX symlink flip")
def test_flip_data_handle_posix_symlinks_to_new_generation(tmp_path):
    fixed = tmp_path / "serve.sock"
    gen1 = tmp_path / "serve-g1.sock"
    gen2 = tmp_path / "serve-g2.sock"
    gen1.touch()
    gen2.touch()

    flip_data_handle(gen1, legacy_path=fixed)
    assert fixed.is_symlink()
    assert os.readlink(fixed) == str(gen1)

    # Flipping again (the next cutover) must atomically repoint, not fail
    # because the link already exists.
    flip_data_handle(gen2, legacy_path=fixed)
    assert os.readlink(fixed) == str(gen2)


@pytest.mark.skipif(not _HAS_AF_UNIX, reason="POSIX symlink flip")
def test_flip_data_handle_posix_upgrades_a_plain_socket_file(tmp_path):
    """The very first cutover on a machine finds the fixed handle as a real
    (pre-cutover-era) socket file, not yet a symlink -- the flip must still
    work, replacing it with a symlink to the new generation."""
    fixed = tmp_path / "serve.sock"
    real_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    real_sock.bind(str(fixed))
    real_sock.close()
    assert fixed.exists() and not fixed.is_symlink()

    gen1 = tmp_path / "serve-g1.sock"
    gen1.touch()
    flip_data_handle(gen1, legacy_path=fixed)
    assert fixed.is_symlink()
    assert os.readlink(fixed) == str(gen1)


@pytest.mark.skipif(_HAS_AF_UNIX, reason="Windows endpoint-sidecar flip")
def test_flip_data_handle_windows_rewrites_endpoint_sidecar(tmp_path):
    fixed = tmp_path / "serve.sock"
    new = tmp_path / "serve-g1.sock"
    (tmp_path / "serve-g1.sock.endpoint").write_text(
        json.dumps({"port": 12345, "token": "abc"}), encoding="utf-8")
    flip_data_handle(new, legacy_path=fixed)
    data = json.loads((tmp_path / "serve.sock.endpoint").read_text(encoding="utf-8"))
    assert data == {"port": 12345, "token": "abc"}


# ── end-to-end: real subprocess cutover ───────────────────────────────────


@pytest.mark.skipif(not _HAS_AF_UNIX, reason="POSIX subprocess cutover")
@pytest.mark.timeout(60)
def test_end_to_end_cutover_retires_old_and_flips_fixed_handle(tmp_path, monkeypatch):
    """The regression this whole module exists to prevent: after a cutover,
    exactly one daemon process is left running, the *old* one has actually
    exited (not just been asked to), and the fixed client-facing handle a
    plain data-plane client dials -- unaware anything happened -- resolves
    to the new generation."""
    monkeypatch.setenv("AGENT_MCP_HOME", str(tmp_path))
    env = dict(os.environ)

    old = subprocess.Popen(
        [sys.executable, "-m", "agent_mcp", "serve", "--idle-timeout", "0"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + 10
        fixed = tmp_path / "serve.sock"
        while time.monotonic() < deadline and not fixed.exists():
            time.sleep(0.05)
        assert fixed.exists(), "old daemon never bound the fixed handle"
        # Let it finish publishing its control endpoint too.
        active_json = tmp_path / "active.json"
        while time.monotonic() < deadline and not active_json.exists():
            time.sleep(0.05)
        assert active_json.exists()

        result = _cutover.run_cutover(health_timeout=15, drain_timeout=15)
        assert result["ok"] is True, result
        assert result["committed"] is True

        # The old process must have actually exited -- not merely been asked
        # to (the exact bug: a wrong/stale control token made "shutdown
        # requested" succeed at the transport level while the real daemon
        # silently rejected it and kept running forever).
        exited = old.wait(timeout=10)
        assert exited is not None

        new_target = os.readlink(fixed)
        assert new_target != str(fixed)

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(str(fixed))
        try:
            sock.sendall((json.dumps({"op": "ping"}) + "\n").encode())
            sock.settimeout(5)
            reply = json.loads(sock.recv(4096).decode())
        finally:
            sock.close()
        assert reply == {"ok": True, "pong": True, "sessions": 0, "attached": 0}
    finally:
        if old.poll() is None:
            old.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                old.wait(timeout=5)
        # Reap the promoted generation too (test isolation; a real deploy
        # leaves it running).
        _reap_promoted(tmp_path)


def _reap_promoted(config_dir) -> None:
    """Best-effort test cleanup: terminate whatever the routing table says
    is active, if anything, so a failed assertion never leaks a daemon."""
    from zdd import routing
    try:
        active = routing.read_active_endpoint(config_dir, verify_listener=False)
    except Exception:
        return
    if active is None or active.pid is None:
        return
    try:
        os.kill(active.pid, 15)
    except OSError:
        pass
