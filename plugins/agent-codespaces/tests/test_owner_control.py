"""Cooperative shutdown targets one owner generation and retains its cleanup."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from agent_codespaces import connection_owner as owner


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(owner, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(owner, "LIVE_FILE", tmp_path / "connection-owner.live.json")
    monkeypatch.setattr(owner, "_LOCK_FILE", tmp_path / "connection-owner.lock")
    monkeypatch.setattr(owner, "ensure_runtime_dir", lambda: None)
    monkeypatch.setattr(owner, "_this_host", lambda: "example")
    return tmp_path


@pytest.mark.parametrize("change", ("legacy", "unknown", "reused", "foreign"))
def test_stop_refuses_unproved_or_unsupported_owner_without_writing(
    state: Path, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    from agent_codespaces import owner_control as control

    owner._write_liveness(1)
    raw = json.loads(owner.LIVE_FILE.read_text())
    raw["owner_control_version"] = 1
    identity = raw["process_identity"]
    if change == "legacy":
        raw.pop("owner_control_version")
    elif change == "foreign":
        raw["host"] = "another-host"
    else:
        monkeypatch.setattr(owner, "owner_process_identity",
                            lambda pid: None if change == "unknown" else identity + "-reused")
    owner.LIVE_FILE.write_text(json.dumps(raw))
    with pytest.raises(RuntimeError):
        control.request_owner_stop(timeout=.1)
    assert not (state / "connection-owner.stop.json").exists()


def test_stop_timeout_retains_request_without_claiming_cleanup(state: Path) -> None:
    from agent_codespaces import owner_control as control

    owner._write_liveness(1)
    with pytest.raises(TimeoutError, match="cleanup"):
        control.request_owner_stop(timeout=.02, poll=.001)
    request = json.loads((state / "connection-owner.stop.json").read_text())
    assert request["state"] == "requested"
    assert request["pid"] == os.getpid()


def test_stale_request_never_stops_a_successor(state: Path) -> None:
    from agent_codespaces import owner_control as control

    owner._write_liveness(1)
    with pytest.raises(TimeoutError):
        control.request_owner_stop(timeout=.01, poll=.001)
    path = state / "connection-owner.stop.json"
    raw = json.loads(path.read_text())
    raw["process_identity"] += "-predecessor"
    path.write_text(json.dumps(raw))
    with owner._owner_lock():
        assert not control.stop_requested()
    control.complete_owner_stop(succeeded=True)
    assert json.loads(path.read_text())["state"] == "requested"


def test_changed_acknowledgement_never_counts_as_target_cleanup(
    state: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_codespaces import owner_control as control

    owner._write_liveness(1)
    read = control._read

    def changed() -> dict:
        value = read()
        return {**value, "pid": -1, "state": "stopped"} if value else value

    monkeypatch.setattr(control, "_read", changed)
    with pytest.raises(RuntimeError, match="another generation"):
        control.request_owner_stop(timeout=.1)


@pytest.mark.parametrize("fail_cleanup", (False, True))
def test_stop_waits_for_normal_channel_cleanup(
    state: Path, monkeypatch: pytest.MonkeyPatch, fail_cleanup: bool,
) -> None:
    from agent_codespaces import owner_control as control

    class Channels:
        def active_codespaces(self) -> set[str]:
            return set()

        async def reconcile(self) -> None:
            await asyncio.sleep(.001)

        async def shutdown(self) -> None:
            cleanup_started.set()
            await release_cleanup.wait()
            if fail_cleanup:
                raise RuntimeError("channel cleanup failed")

    # Admission is covered by native daemon tests; this case controls only the
    # existing loop's stop/cleanup ordering and never starts a resident process.
    monkeypatch.setattr(owner, "list_holds", lambda: [])

    async def exercise() -> None:
        nonlocal cleanup_started, release_cleanup
        cleanup_started, release_cleanup = asyncio.Event(), asyncio.Event()
        daemon = asyncio.create_task(owner.run_owner_daemon.__wrapped__(Channels(), interval=.01))
        while owner.read_liveness() is None:
            await asyncio.sleep(.001)
        stop = asyncio.create_task(asyncio.to_thread(control.request_owner_stop, timeout=5, poll=.005))
        await asyncio.wait_for(cleanup_started.wait(), timeout=3)
        assert not stop.done(), "Stop acknowledged before channels settled"
        release_cleanup.set()
        if fail_cleanup:
            with pytest.raises(RuntimeError, match="channel cleanup"):
                await daemon
            with pytest.raises(RuntimeError, match="cleanup failed"):
                await stop
        else:
            await daemon
            result = await stop
            assert result["status"] == "stopped"
            assert result["pid"] == os.getpid()
        assert not owner.LIVE_FILE.exists()

    cleanup_started: asyncio.Event
    release_cleanup: asyncio.Event
    asyncio.run(exercise())


def test_owner_stop_reaches_real_cli_parser(
    state: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from agent_codespaces import __main__ as cli
    from agent_codespaces import owner_control as control

    monkeypatch.setattr(control, "request_owner_stop", lambda: {"status": "absent"})
    assert cli.main(["owner", "--stop"]) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "absent"}


@pytest.mark.skipif(os.name != "nt", reason="Windows delete-pending lock contention")
def test_owner_lock_retries_transient_access_denied_without_stealing(
    state: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = os.open
    calls = 0

    def open_lock(path: str, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal calls
        if Path(path) == owner._LOCK_FILE:
            calls += 1
            if calls == 1:
                raise PermissionError("delete pending")
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", open_lock)
    with owner._owner_lock(timeout=.1, poll=.001):
        assert calls == 2


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
def test_real_cli_stop_settles_native_owner_and_its_channels(tmp_path: Path) -> None:
    from agent_procutil import no_window_kwargs
    from agent_procutil.daemon_instances import daemon_test_lifecycle

    script = """
import asyncio, sys
from pathlib import Path
from agent_codespaces import connection_owner as owner
root = Path(sys.argv[1])
owner.RUNTIME_DIR = root
owner.LIVE_FILE = root / 'connection-owner.live.json'
owner.OWNER_FILE = root / 'connection-owner.json'
owner._LOCK_FILE = root / 'connection-owner.lock'
owner.ensure_runtime_dir = lambda: None
class Channels:
    def active_codespaces(self): return set()
    async def reconcile(self): await asyncio.sleep(.01)
    async def shutdown(self):
        await asyncio.sleep(.1)
        (root / 'channels-closed').write_text('settled', encoding='ascii')
async def main():
    stop = asyncio.Event()
    asyncio.get_running_loop().call_later(20, stop.set)
    await owner.run_owner_daemon(Channels(), interval=.1, stop_event=stop)
asyncio.run(main())
"""
    with daemon_test_lifecycle(owner="owner-cooperative-stop", registry_root=tmp_path / "instances") as lifecycle:
        env = {**os.environ, **lifecycle, "AGENT_CODESPACES_HOME": str(tmp_path)}
        with subprocess.Popen(
            [sys.executable, "-I", "-X", "utf8", "-c", script, str(tmp_path)],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            **no_window_kwargs(),
        ) as process:
            try:
                deadline = time.monotonic() + 8
                beacon = tmp_path / "connection-owner.live.json"
                while not beacon.exists() and time.monotonic() < deadline and process.poll() is None:
                    time.sleep(.02)
                assert beacon.exists(), "Native owner did not publish readiness"
                target = json.loads(beacon.read_text())
                result = subprocess.run(
                    [sys.executable, "-I", "-X", "utf8", "-m", "agent_codespaces", "owner", "--stop"],
                    env=env, capture_output=True, text=True, timeout=15, **no_window_kwargs(),
                )
                assert result.returncode == 0, result.stdout + result.stderr
                response = json.loads(result.stdout)
                assert response["status"] == "stopped"
                assert response["pid"] == target["pid"]
                assert response["process_identity"] == target["process_identity"]
                assert (tmp_path / "channels-closed").read_text() == "settled"
            finally:
                stdout, stderr = process.communicate(timeout=25)
            assert process.returncode == 0, stdout + stderr
            assert not beacon.exists()
