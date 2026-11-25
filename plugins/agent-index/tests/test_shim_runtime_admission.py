"""Shim admission keeps native lookup custody without changing worker ownership."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from pathlib import Path
import subprocess
import sys
import threading
import traceback
from types import SimpleNamespace
from typing import Any, NoReturn

import httpx
import pytest
from agent_procutil.owned_process import OutputLimitExceeded, OwnedCleanupError

from agent_index.engine import shim


def forbidden(*args: object, **kwargs: object) -> NoReturn:
    pytest.fail("Refused admission must not spawn, retry cleanup, or replace the worker")


@pytest.mark.parametrize("override", [None, "invalid", "{}", '["custom", "literal spaces", ""]'])
def test_default_admission_and_explicit_prefix(
    monkeypatch: pytest.MonkeyPatch, override: str | None,
) -> None:
    calls = []

    def admit(python: str, *, uv: str | None) -> str:
        calls.append((python, uv))
        return python

    monkeypatch.setattr(shim, "resolve_runtime_python", admit)
    monkeypatch.setenv("AGENT_INDEX_UV", "bound UV")
    if override is None:
        monkeypatch.delenv("AGENT_INDEX_ENGINE_WORKER_CMD", raising=False)
    else:
        monkeypatch.setenv("AGENT_INDEX_ENGINE_WORKER_CMD", override)
    command = shim._worker_command("127.0.0.7", 19421)
    if override and override.startswith("["):
        assert command == ["custom", "literal spaces", "", "--host", "127.0.0.7", "--port", "19421"]
        assert not calls
    else:
        assert calls == [(sys.executable, "bound UV")]
        assert command == [
            sys.executable, "-I", "-B", "-m", "agent_index_engine.app",
            "--host", "127.0.0.7", "--port", "19421",
        ]


def cleanup_error() -> OwnedCleanupError:
    return OwnedCleanupError(
        SimpleNamespace(pid=123, returncode=None), object(),
        cleanup_resources=(("output", object()),), cleanup_retry=forbidden,
    )


@pytest.mark.parametrize("failure", ["missing", "mismatch", "timeout", "output", "decode", "cleanup"])
def test_refusal_precedes_spawn_and_retains_unconfirmed_cleanup(
    monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    errors = {
        "missing": FileNotFoundError("UV is unavailable"),
        "mismatch": RuntimeError("UV selected another runtime"),
        "timeout": subprocess.TimeoutExpired(["uv"], 10),
        "output": OutputLimitExceeded("stdout", 65536),
        "decode": UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"),
        "cleanup": cleanup_error(),
    }
    original = errors[failure]
    calls = []

    def refuse(*args: object, **kwargs: object) -> NoReturn:
        calls.append("lookup")
        raise original

    monkeypatch.delenv("AGENT_INDEX_ENGINE_WORKER_CMD", raising=False)
    monkeypatch.setattr(shim, "resolve_runtime_python", refuse)
    monkeypatch.setattr(shim, "spawn_instance", forbidden)

    async def run() -> None:
        supervisor = shim.WorkerSupervisor()
        async with httpx.AsyncClient() as probe:
            with pytest.raises(type(original)) as caught:
                await supervisor.ensure(probe)
            assert caught.value is original and not supervisor.running()
            if failure == "cleanup":
                operations: tuple[Callable[[], Awaitable[None]], ...] = (
                    lambda: supervisor.ensure(probe), supervisor.stop,
                )
                for operation in operations:
                    with pytest.raises(OwnedCleanupError) as retained:
                        await operation()
                    assert retained.value is original
                assert calls == ["lookup"]
            else:
                await supervisor.stop()

    asyncio.run(run())


@pytest.mark.parametrize("outcome", ["success", "cleanup"])
def test_repeated_cancellation_waits_for_lookup_and_prevents_replacement(
    monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    entered, release = threading.Event(), threading.Event()
    original = cleanup_error()
    calls = []

    def lookup(*args: object, **kwargs: object) -> str:
        calls.append("lookup")
        entered.set()
        assert release.wait(5), "fixture did not release its native lookup"
        if outcome == "cleanup":
            raise original
        return sys.executable

    monkeypatch.delenv("AGENT_INDEX_ENGINE_WORKER_CMD", raising=False)
    monkeypatch.setattr(shim, "resolve_runtime_python", lookup)
    monkeypatch.setattr(shim, "spawn_instance", forbidden)

    async def run() -> None:
        supervisor = shim.WorkerSupervisor()
        async with httpx.AsyncClient() as probe:
            pending = asyncio.create_task(supervisor.ensure(probe))
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                pending.cancel()
                await asyncio.sleep(0)
                pending.cancel()
                await asyncio.sleep(0)
                assert not pending.done() and supervisor._lock.locked()
            finally:
                release.set()
            with pytest.raises(OwnedCleanupError if outcome == "cleanup" else asyncio.CancelledError) as caught:
                await pending
            if outcome == "cleanup":
                assert caught.value is original
                with pytest.raises(OwnedCleanupError) as retained:
                    await supervisor.stop()
                assert retained.value is original
            else:
                await supervisor.stop()
            assert calls == ["lookup"] and not supervisor.running()

    asyncio.run(run())


def test_concurrent_requests_share_existing_spawn_and_stop_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    proc: Any = SimpleNamespace(returncode=None)
    proc.poll = lambda: proc.returncode

    def terminate() -> None:
        events.append("terminate")
        proc.returncode = 0

    proc.terminate = terminate

    def wait(timeout: float | None = None) -> int:
        events.append("wait")
        return 0

    proc.wait = wait

    def admit(*args: object, **kwargs: object) -> str:
        events.append("lookup")
        return sys.executable

    def spawn(command: list[str], **kwargs: Any) -> Any:
        events.append("spawn")
        assert command[:3] == [sys.executable, "-I", "-B"]
        assert kwargs["env"]["AGENT_INDEX_ENGINE_IDLE_TIMEOUT"] == "0"
        assert kwargs["instance"].runtime_root == Path(shim.__file__).absolute().parent
        return proc

    async def healthy(*args: object, **kwargs: object) -> Any:
        await asyncio.sleep(0)
        return SimpleNamespace(status_code=200)

    monkeypatch.delenv("AGENT_INDEX_ENGINE_WORKER_CMD", raising=False)
    monkeypatch.setattr(shim, "resolve_runtime_python", admit)
    monkeypatch.setattr(shim, "spawn_instance", spawn)

    async def run() -> None:
        supervisor = shim.WorkerSupervisor()
        probe: Any = SimpleNamespace(get=healthy)
        await asyncio.gather(supervisor.ensure(probe), supervisor.ensure(probe))
        assert supervisor.running()
        await supervisor.stop()
        assert not supervisor.running()

    asyncio.run(run())
    assert events == ["lookup", "spawn", "terminate", "wait"]


def test_cleanup_custody_escapes_http_and_shutdown_closes_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_procutil import daemon_instances

    original = cleanup_error()
    calls = []

    def refuse(*args: object, **kwargs: object) -> NoReturn:
        calls.append("lookup")
        raise original

    monkeypatch.setattr(daemon_instances, "admit_daemon_instance", lambda *a, **k: nullcontext())
    monkeypatch.delenv("AGENT_INDEX_ENGINE_WORKER_CMD", raising=False)
    monkeypatch.setattr(shim, "resolve_runtime_python", refuse)
    monkeypatch.setattr(shim, "spawn_instance", forbidden)

    async def run() -> None:
        app = shim.create_app(idle_timeout=0)
        with pytest.raises(OwnedCleanupError) as shutdown:
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://fixture",
                ) as client:
                    for _ in range(2):
                        with pytest.raises(OwnedCleanupError) as request:
                            await client.post("/embed", json={})
                        assert request.value is original
                    assert (await client.get("/health")).status_code == 200
        assert shutdown.value is original and app.state.client.is_closed

    asyncio.run(run())
    assert calls == ["lookup"]


@pytest.mark.parametrize("error", [
    FileNotFoundError("UV is unavailable"),
    RuntimeError("UV selected another runtime"),
    subprocess.TimeoutExpired(["uv"], 10),
    OutputLimitExceeded("stdout", 65536),
    UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"),
])
def test_settled_admission_failure_keeps_worker_unavailable_http_contract(
    monkeypatch: pytest.MonkeyPatch, error: Exception,
) -> None:
    from agent_procutil import daemon_instances

    def refuse(*args: object, **kwargs: object) -> NoReturn:
        raise error

    monkeypatch.setattr(daemon_instances, "admit_daemon_instance", lambda *a, **k: nullcontext())
    monkeypatch.delenv("AGENT_INDEX_ENGINE_WORKER_CMD", raising=False)
    monkeypatch.setattr(shim, "resolve_runtime_python", refuse)
    monkeypatch.setattr(shim, "spawn_instance", forbidden)

    async def run() -> None:
        app = shim.create_app(idle_timeout=0)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://fixture",
            ) as client:
                response = await client.post("/embed", json={})
                assert response.status_code == 503
                assert "GPU worker unavailable" in response.json()["detail"]
        assert app.state.client.is_closed

    asyncio.run(run())


def test_repeated_custody_errors_do_not_accumulate_request_tracebacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = cleanup_error()

    def refuse(*args: object, **kwargs: object) -> NoReturn:
        raise original

    monkeypatch.delenv("AGENT_INDEX_ENGINE_WORKER_CMD", raising=False)
    monkeypatch.setattr(shim, "resolve_runtime_python", refuse)
    monkeypatch.setattr(shim, "spawn_instance", forbidden)

    async def run() -> None:
        supervisor = shim.WorkerSupervisor()
        async with httpx.AsyncClient() as probe:
            with pytest.raises(OwnedCleanupError):
                await supervisor.ensure(probe)
            for operation in ("ensure", "stop"):
                depths = []
                for _ in range(4):
                    with pytest.raises(OwnedCleanupError) as caught:
                        if operation == "ensure":
                            await supervisor.ensure(probe)
                        else:
                            await supervisor.stop()
                    assert caught.value is original
                    frames = list(traceback.walk_tb(original.__traceback__))
                    assert sum(frame.f_code.co_name == operation for frame, _ in frames) == 1
                    assert not any(frame.f_code.co_name == "_command" for frame, _ in frames)
                    depths.append(len(frames))
                assert len(set(depths)) == 1

    asyncio.run(run())
