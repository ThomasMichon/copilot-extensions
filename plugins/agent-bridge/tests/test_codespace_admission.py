"""Caller-contained provider processes; no live credentials, claims or venues."""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import sys
import threading
from types import SimpleNamespace

import pytest
import uvicorn
from fastapi import FastAPI

from agent_bridge import session_manager as core
from agent_bridge.routes.health import router as health_router
from agent_bridge.session_host.codespace_transport import (
    codespace_launch_ownership,
    codespace_provider_call,
)
from agent_bridge.watchdog import ServingWatchdog, _make_health_probe


async def _until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.01)


def test_release_does_not_inherit_the_providers_noop_admission_flag(tmp_path, monkeypatch):
    claim = tmp_path / "synthetic-claim"
    claim.touch()
    script = (
        "import os, pathlib, sys\n"
        "if os.environ.get('AGENT_CODESPACES_DISABLE_CLAIM'): sys.exit(0)\n"
        "pathlib.Path(sys.argv[1]).unlink()\n"
    )
    monkeypatch.setenv("AGENT_CODESPACES_DISABLE_CLAIM", "1")
    monkeypatch.setattr(
        core, "_codespace_provider_command",
        lambda: [sys.executable, "-c", script, str(claim)],
    )
    assert core._release_codespace_claim("synthetic", "/task/owner") is True
    assert not claim.exists(), "provider reported success without removing its claim"


@pytest.fixture
def provider(tmp_path, monkeypatch):
    locks = tmp_path / "locks"
    locks.mkdir()
    monkeypatch.setattr("ssh_manager.locks.locks_dir", lambda: locks)
    script = (
        "import pathlib, sys, time\n"
        "root = pathlib.Path(sys.argv[1]); kind = sys.argv[2]\n"
        "(root / (kind + '.entered')).touch()\n"
        "deadline = time.monotonic() + 4\n"
        "while not (root / (kind + '.release')).exists():\n"
        "    if time.monotonic() >= deadline: sys.exit(9)\n"
        "    time.sleep(0.01)\n"
        "print('settled')\n"
        "sys.exit(int(sys.argv[3]))\n"
    )
    calls = []
    completed = []
    exit_codes = {"claim": 0, "release": 0, "relay": 0}
    for kind in exit_codes:
        (tmp_path / f"{kind}.release").touch()

    def run(kind, name, owner=None, **kwargs):
        calls.append((kind, name, owner, kwargs))
        command = [sys.executable, "-c", script, str(tmp_path), kind, str(exit_codes[kind])]
        if kind == "claim":
            result = core._run_venue_claim(command, target_name=name, kind="synthetic")
        else:
            process = core.run_captured(command, timeout=5)
            result = process.returncode == 0
            if kind == "relay":
                assert result
                result = ("", None)
        completed.append(kind)
        return result

    def block(kind):
        (tmp_path / f"{kind}.release").unlink()

    def unblock(kind):
        (tmp_path / f"{kind}.release").touch()

    owner = SimpleNamespace(
        _claim_codespace=lambda *a, **k: run("claim", *a, **k),
        _release_codespace_claim=lambda *a: run("release", *a),
        _resolve_relay_launch_env=lambda name, port: run("relay", name),
        block=block, unblock=unblock,
        entered=lambda kind: (tmp_path / f"{kind}.entered").exists(),
        calls=calls, completed=completed, exit_codes=exit_codes,
    )
    yield owner
    for kind in exit_codes:
        unblock(kind)
    assert sorted(completed) == sorted(call[0] for call in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("slow", ["claim", "relay", "release"])
async def test_slow_provider_keeps_real_health_and_watchdog_responsive(
    session_manager, provider, slow,
):
    app = FastAPI()
    app.include_router(health_router)
    app.state.session_manager = session_manager
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="error"))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    stopped = threading.Event()
    probes = []
    replacements = []
    probe = _make_health_probe("127.0.0.1", port, timeout=0.1)

    def health():
        result = probe()
        probes.append(result)
        return result

    watchdog = ServingWatchdog(
        is_started=lambda: server.started,
        is_shutting_down=stopped.is_set, is_stopped=stopped.is_set,
        probe_serving=health, on_dead=replacements.append,
        interval=0.02, serving_grace=0.1, sleep=stopped.wait,
    )
    thread = threading.Thread(target=watchdog.run)
    provider.block(slow)

    async def launch():
        async with codespace_launch_ownership(
            session_manager, "synthetic", {"name": "synthetic"}, "/task/owner", provider,
        ):
            async with codespace_provider_call(
                provider._resolve_relay_launch_env, "synthetic", None,
            ):
                pass
            raise RuntimeError("synthetic preflight failure")

    operation = None
    try:
        await _until(lambda: server.started)
        # The first request pays one-time app/import cold start on the serving loop;
        # absorb it so the tight probes measure only the blocked provider call.
        warm = _make_health_probe("127.0.0.1", port, timeout=10)
        assert await asyncio.to_thread(warm)
        thread.start()
        operation = asyncio.create_task(launch())
        await _until(lambda: provider.entered(slow))
        await asyncio.sleep(0.3)
        assert not operation.done(), "provider did not stay blocked during health observations"
        assert len(probes) >= 3 and all(probes)
        assert not replacements
        provider.unblock(slow)
        with pytest.raises(RuntimeError, match="synthetic preflight failure"):
            await operation
        assert not session_manager._codespace_lock_sessions
    finally:
        provider.unblock(slow)
        if operation is not None:
            with contextlib.suppress(BaseException):
                await operation
        stopped.set()
        if thread.ident is not None:
            await asyncio.to_thread(thread.join, 2)
            assert not thread.is_alive()
        server.should_exit = True
        await asyncio.wait_for(serving, 5)
        listener.close()
        session_manager._release_codespace_lock("synthetic")


@pytest.mark.asyncio
@pytest.mark.parametrize("release_ok", [False, True])
async def test_repeated_cancellation_settles_original_claim_and_rollback_receipt(
    session_manager, provider, release_ok,
):
    provider.block("claim")
    provider.block("release")
    provider.exit_codes["release"] = 0 if release_ok else 2
    metadata = {}
    persisted = []
    owner_thread = threading.get_ident()

    def persist():
        assert threading.get_ident() == owner_thread
        persisted.append(json.loads(json.dumps(metadata)))

    async def launch():
        async with codespace_launch_ownership(
            session_manager, "cancelled", {"name": "synthetic"}, "/task/owner", provider,
            holder_ref="machine/project/worktree",
            claim_metadata=metadata, persist_claim_metadata=persist,
        ):
            pytest.fail("cancelled admission must not connect or replay a prompt")

    operation = asyncio.create_task(launch())
    try:
        await _until(lambda: provider.entered("claim"))
        for _ in range(2):
            operation.cancel()
            await asyncio.sleep(0)
        assert not operation.done()
        assert session_manager._codespace_lock_sessions == {"cancelled": "synthetic"}
        provider.unblock("claim")
        await _until(lambda: provider.entered("release"))
        identity = {"name": "synthetic", "owner": "/task/owner"}
        assert metadata["codespace_claim"] == identity
        assert persisted == [{"codespace_claim": None}, {"codespace_claim": identity}]
        for _ in range(2):
            operation.cancel()
            await asyncio.sleep(0)
        assert not operation.done()
        provider.unblock("release")
        expected = asyncio.CancelledError if release_ok else RuntimeError
        with pytest.raises(expected):
            await operation
        assert provider.completed == ["claim", "release"]
        assert [call[:3] for call in provider.calls] == [
            ("claim", "synthetic", "/task/owner"),
            ("release", "synthetic", "/task/owner"),
        ]
        assert provider.calls[0][3]["holder_ref"] == "machine/project/worktree"
        assert bool(session_manager._codespace_lock_sessions) is not release_ok
        assert metadata["codespace_claim"] == (None if release_ok else identity)
    finally:
        provider.unblock("claim")
        provider.unblock("release")
        with contextlib.suppress(BaseException):
            await operation
        session_manager._release_codespace_lock("cancelled")
