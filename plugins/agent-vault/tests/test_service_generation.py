"""Incoming-first endpoints retain native request and writer custody."""

from __future__ import annotations

import json
import asyncio
from collections.abc import Callable
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from agent_procutil import no_window_kwargs
from agent_vault import service_generation as generation
from zdd.private_peer import PeerProcess
from single_instance_lease import AlreadyRunningError


def test_generation_identity_follows_the_selected_build_not_version_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "vault"
    first, second = root / "versions" / "9.0.0", root / "versions" / "1.0.0"
    for slot, identity in ((first, "a" * 32), (second, "b" * 32)):
        (slot / ".runtime-bootstrap").mkdir(parents=True)
        (slot / ".runtime-bootstrap" / "runtime-generation").write_text(identity)
    monkeypatch.setattr(generation, "home_dir", lambda: root)
    monkeypatch.setattr(generation, "PROCESS_SLOT", first)
    monkeypatch.setattr(generation, "PROCESS_GENERATION", "a" * 32)
    (root / "current-version").write_text("9.0.0")
    assert generation.selected()
    (root / "current-version").write_text("1.0.0")
    assert generation.selected_generation() == "b" * 32
    assert not generation.selected()
    assert not generation.same_generation({
        "ok": True, "generation_protocol": generation.PROTOCOL,
        "runtime_generation": "b" * 32,
    })


@pytest.mark.parametrize("selection", ("../foreign", "..", "/foreign", "bad\nname"))
def test_selection_cannot_resolve_an_external_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selection: str,
) -> None:
    (tmp_path / "current-version").write_text(selection)
    monkeypatch.setattr(generation, "home_dir", lambda: tmp_path)
    assert generation.selected_generation() is None


def test_confirmed_retirement_is_not_reactivated_when_the_successor_disappears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = object.__new__(generation.ServiceGeneration)
    controller.service = SimpleNamespace(_runtime_can_work=True, _shutdown=False)
    controller.connections = SimpleNamespace(work={object()})
    controller.retiring = {"runtime_generation": "displaced-successor"}
    monkeypatch.setattr(generation, "selected_generation", lambda: "next-successor")
    asyncio.run(controller.tick())
    assert not controller.service._runtime_can_work
    assert not controller.service._shutdown
    controller.connections.work.clear()
    asyncio.run(controller.tick())
    assert controller.service._shutdown
    assert not controller.service._runtime_can_work


@pytest.mark.parametrize("response", (
    [1], {"ok": True, "pid": 123, "generation_protocol": True, "runtime_generation": "other"},
    {"ok": True, "pid": 123, "generation_protocol": 1, "runtime_generation": None},
    {"ok": True, "pid": 123, "generation_protocol": 1, "runtime_generation": ""},
))
def test_malformed_successor_health_cannot_retire_the_incumbent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, response: object,
) -> None:
    controller = object.__new__(generation.ServiceGeneration)
    controller.root = tmp_path
    controller.retiring = None
    controller.superseded = 0
    controller.service = SimpleNamespace(_runtime_can_work=True, _shutdown=False)
    monkeypatch.setattr(generation, "read_endpoint", lambda _: SimpleNamespace(pid=123))
    monkeypatch.setattr(generation, "endpoint_health", lambda _: response)
    monkeypatch.setattr(generation, "selected_generation", lambda: None)
    for _ in range(2):
        asyncio.run(controller.tick())
    assert not controller.service._shutdown
    assert controller.service._runtime_can_work


@pytest.mark.parametrize("acquired", (False, True))
def test_publication_contention_retries_and_rechecks_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, acquired: bool,
) -> None:
    events = []

    class Publication:
        def acquire(self) -> None:
            events.append("acquire")
            if len(events) == 1 or not acquired:
                raise AlreadyRunningError(tmp_path / "publication.lock", 123)

        def release(self) -> None:
            events.append("release")

    controller = object.__new__(generation.ServiceGeneration)
    controller.api = SimpleNamespace()
    controller.service = SimpleNamespace(_runtime_can_work=True, _shutdown=False)
    controller.generation_lease = SimpleNamespace(acquire=lambda: None)
    controller.publication = Publication()
    selections = iter((True, False))
    monkeypatch.setattr(generation, "selected", lambda: next(selections))
    asyncio.run(controller.start(0))
    assert events == ["acquire", "acquire", *(["release"] if acquired else [])]
    assert controller.has_generation
    assert controller.service._shutdown
    assert not controller.service._runtime_can_work


def test_unix_health_deadline_covers_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_vault import service

    events = []

    class Probe:
        def settimeout(self, value: float) -> None:
            events.append(("timeout", value))

        def connect(self, path: str) -> None:
            events.append(("connect", path))
            raise TimeoutError("fixture connect expired")

    monkeypatch.setattr(socket, "AF_UNIX", object(), raising=False)
    monkeypatch.setattr(socket, "socket", lambda *args: Probe())
    assert service._dial_unix({"action": "ping"}, "/fixture/vault.sock", timeout=.25) is None
    assert events == [("timeout", .25), ("connect", "/fixture/vault.sock")]


CHILD = r"""
import asyncio, hashlib, json, os, sys, time
from pathlib import Path
from types import SimpleNamespace

root, identity, port, mode = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), sys.argv[4]
os.environ.update(
    AGENT_VAULT_HOME=str(root), AGENT_VAULT_RUN_DIR=str(root / "run"),
    AGENT_VAULT_PID=str(root / "vault.pid"), AGENT_VAULT_SOCKET=str(root / "vault.sock"),
    AGENT_VAULT_PIPE=r"\\.\pipe\vault-generation-" + hashlib.sha256(str(root).encode()).hexdigest()[:20],
    AGENT_VAULT_LOG=str(root / (identity + ".log")), AGENT_VAULT_PORT=str(port),
)
os.environ.pop("AGENT_VAULT_INSTALLATION_ID", None)
from agent_vault import service, service_generation as generation
from agent_vault import service_handoff
generation.PROCESS_GENERATION = service.PROCESS_GENERATION = identity
generation.selected_generation = lambda: (root / "selection").read_text().strip()
generation.selected = lambda: generation.selected_generation() == identity
service.get_registry = lambda: SimpleNamespace(run_startup=lambda *args: None)
vault = service.VaultService()
vault.ttl_override = 0
vault.cli.status = lambda *args: "not_found"
service.PASSWORD_TTL = 300
if identity == "newer":
    kpdb = str(root / "fixture.kdbx")
    vault.cli.set_password(kpdb, "fixture-master")
    vault._password_set_at[kpdb] = time.time() - 60
if mode == "no-overlap":
    service.supported_versions = service_handoff.supported_versions = lambda: [99]

def native(kpdb, entry, group=None):
    with (root / "work.jsonl").open("a") as stream:
        stream.write(json.dumps([identity, "entered"]) + "\n")
    (root / (identity + ".entered")).touch()
    deadline = time.monotonic() + 15
    while not (root / (identity + ".release")).exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("Owned fixture work was not released")
        time.sleep(.01)
    with (root / "work.jsonl").open("a") as stream:
        stream.write(json.dumps([identity, "settled"]) + "\n")
    return True

vault.has = native
if mode == "fail":
    async def fail(*args, **kwargs):
        raise OSError("fixture listener construction failed")
    asyncio.start_server = fail
async def main():
    async def stop_owned():
        while not (root / (identity + ".stop")).exists():
            await asyncio.sleep(.02)
        vault._shutdown = True
    stopped = asyncio.create_task(stop_owned())
    try:
        await service.run_server(vault, tcp_port=port)
    finally:
        stopped.cancel()
        try:
            await stopped
        except asyncio.CancelledError:
            pass

asyncio.run(main())
"""


def _endpoint(root: Path) -> dict | None:
    try:
        return json.loads((root / "run" / "endpoint.json").read_text())
    except (OSError, ValueError):
        return None


def _port(endpoint: dict) -> int:
    candidates = [endpoint, *endpoint.get("alt", [])]
    return next(
        int(item["endpoint"].rsplit(":", 1)[1])
        for item in candidates if item["transport"] == "tcp"
    )


def _send(port: int, request: dict) -> dict:
    with socket.create_connection(("127.0.0.1", port), timeout=1) as connection:
        connection.settimeout(10)
        connection.sendall((json.dumps(request) + "\n").encode())
        with connection.makefile("rb") as stream:
            return json.loads(stream.readline())


def _until(predicate: Callable[[], bool], processes: list[subprocess.Popen], timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        for process in processes:
            if process.poll() is not None:
                stdout, stderr = process.communicate(timeout=1)
                pytest.fail(f"Owned child exited {process.returncode}: {stdout}\n{stderr}")
        if time.monotonic() >= deadline:
            pytest.fail("Owned generation condition did not settle")
        time.sleep(.02)


def _start(root: Path, identity: str, port: int, mode: str = "normal") -> subprocess.Popen:
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("AGENT_VAULT_", "VAULT_")) and key not in {"KPDB", "KEE_PASS_DB"}
    }
    return subprocess.Popen(
        [sys.executable, "-c", CHILD, str(root), identity, str(port), mode],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env, **no_window_kwargs(),
    )


def _settle(root: Path, processes: list[subprocess.Popen]) -> list[str]:
    for identity in ("newer", "older"):
        (root / (identity + ".release")).touch()
        (root / (identity + ".stop")).touch()
    return ["".join(process.communicate(timeout=10)) for process in processes]


def _capture_process(pid: int) -> PeerProcess:
    from zdd.diagnostics import process_start_time
    from zdd.private_protocol import PeerIdentity

    if os.name == "nt":
        from zdd.private_windows_native import native

        api = native()
        handle = api.k.OpenProcess(0x1000 | 0x100000, False, pid)
        assert handle
        return PeerProcess(handle, api.identity(handle, pid), api=api)
    return PeerProcess(
        os.pidfd_open(pid), PeerIdentity(pid, process_start_time(pid), str(os.geteuid())),
    )


@pytest.mark.skipif(os.name != "nt" and sys.platform != "linux", reason="Native process references")
@pytest.mark.parametrize("incoming_mode,warm", (("normal", True), ("no-overlap", False)))
def test_real_successor_is_ready_before_self_retirement_and_cannot_overlap_writers(
    tmp_path: Path, incoming_mode: str, warm: bool,
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from zdd.private_protocol import Deadline
    from zdd.diagnostics import process_start_time

    (tmp_path / "selection").write_text("newer")
    durable = tmp_path / "fixture.kdbx"
    durable.write_bytes(b"fixture durable bytes")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        fixed_port = reservation.getsockname()[1]
    old = _start(tmp_path, "newer", fixed_port)
    children = [old]
    old_lifetime = None
    try:
        _until(lambda: _endpoint(tmp_path) is not None, children)
        initial = _endpoint(tmp_path)
        old_pid = initial["pid"]
        old_identity = process_start_time(old_pid)
        assert old_identity
        old_lifetime = _capture_process(old_pid)
        assert old_lifetime.peer.start_time == old_identity
        assert _send(fixed_port, {"action": "ping"})["accepting_work"]
        with ThreadPoolExecutor(max_workers=1) as worker:
            pending = worker.submit(
                _send, fixed_port, {"action": "has", "kpdb": str(durable), "entry": "fixture"},
            )
            _until(lambda: (tmp_path / "newer.entered").exists(), children)
            (tmp_path / "selection").write_text("older")
            new = _start(tmp_path, "older", fixed_port, incoming_mode)
            children.append(new)
            _until(lambda: (_endpoint(tmp_path) or {}).get("pid") not in {None, old_pid}, children)
            endpoint = _endpoint(tmp_path)
            new_pid = endpoint["pid"]
            assert process_start_time(new_pid)
            new_port = _port(endpoint)
            health = _send(new_port, {"action": "ping"})
            assert health["runtime_generation"] == "older"
            assert not health["accepting_work"]
            assert old.poll() is None
            blocked = _send(new_port, {"action": "has", "kpdb": str(durable), "entry": "fixture"})
            assert blocked == {"ok": False, "error": "service_transition", "retryable": True}
            assert not (tmp_path / "older.entered").exists()
            (tmp_path / "newer.release").touch()
            assert pending.result(timeout=10)["ok"]
        stdout, stderr = old.communicate(timeout=10)
        assert old.returncode == 0, stdout + stderr
        old_lifetime.wait_exit(Deadline(1))
        _until(lambda: _send(new_port, {"action": "ping"})["accepting_work"], [new])
        assert _send(new_port, {"action": "ping"})["unlocked_vaults"] == (
            [str(durable)] if warm else []
        )
        assert _endpoint(tmp_path)["pid"] == new_pid
        assert int((tmp_path / "vault.pid").read_text()) == new_pid
        (tmp_path / "older.release").touch()
        assert _send(new_port, {"action": "has", "kpdb": str(durable), "entry": "fixture"})["ok"]
        assert _send(fixed_port, {"action": "ping"})["pid"] == new_pid
        assert [json.loads(row) for row in (tmp_path / "work.jsonl").read_text().splitlines()] == [
            ["newer", "entered"], ["newer", "settled"], ["older", "entered"], ["older", "settled"],
        ]
        assert durable.read_bytes() == b"fixture durable bytes"
    finally:
        outputs = _settle(tmp_path, children)
        if old_lifetime is not None:
            old_lifetime.close()
    assert all(process.returncode == 0 for process in children)
    assert all("fixture-master" not in output for output in outputs)
    assert all(b"fixture-master" not in path.read_bytes()
               for path in tmp_path.rglob("*") if path.is_file())
    assert _endpoint(tmp_path) is None
    assert not (tmp_path / "vault.pid").exists()


def test_failed_successor_does_not_remove_or_stop_the_incumbent(tmp_path: Path) -> None:
    (tmp_path / "selection").write_text("newer")
    old = _start(tmp_path, "newer", 0)
    children = [old]
    try:
        _until(lambda: _endpoint(tmp_path) is not None, children)
        endpoint = _endpoint(tmp_path)
        old_port = _port(endpoint)
        (tmp_path / "selection").write_text("older")
        failed = _start(tmp_path, "older", 0, "fail")
        children.append(failed)
        stdout, stderr = failed.communicate(timeout=10)
        assert failed.returncode != 0
        assert "fixture listener construction failed" in stderr
        assert _endpoint(tmp_path) == endpoint
        assert _send(old_port, {"action": "ping"})["pid"] == endpoint["pid"]
        assert int((tmp_path / "vault.pid").read_text()) == endpoint["pid"]
        assert old.poll() is None
    finally:
        _settle(tmp_path, children)
    assert old.returncode == 0
