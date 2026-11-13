"""Incoming-first endpoints retain native request and writer custody."""

from __future__ import annotations

import json
import asyncio
import errno
from contextlib import nullcontext
from collections.abc import Callable
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import time
import threading
from types import SimpleNamespace

import pytest

from agent_procutil import no_window_kwargs
from agent_vault import service_generation as generation
from zdd.private_peer import PeerProcess
from single_instance_lease import AlreadyRunningError


@pytest.mark.parametrize("source,client_port,explicit,expected", [
    ("default", 19999, None, 0),
    ("env", 4242, None, 4242),
    ("repo", 4242, None, 4242),
    ("default", 19999, 4343, 4343),
    ("default", 19999, 0, 0),
])
def test_direct_generation_start_applies_listener_default_before_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    source: str, client_port: int, explicit: int | None, expected: int,
) -> None:
    from agent_vault import config, service

    monkeypatch.setattr(service, "run_dir", lambda: tmp_path / "run")
    monkeypatch.setattr(generation, "home_dir", lambda: tmp_path)
    monkeypatch.setenv(config.BIND_PORT_ENV, "0")
    monkeypatch.setattr(
        config, "resolve_context",
        lambda: SimpleNamespace(port=client_port, sources={"port": source}),
    )
    controller = generation.ServiceGeneration(SimpleNamespace(), SimpleNamespace(), [], [])

    def stop_before_binding() -> None:
        raise AlreadyRunningError("synthetic generation; no endpoint may be opened", holder_pid=123)

    controller.generation_lease = SimpleNamespace(acquire=stop_before_binding)
    asyncio.run(controller.start(explicit))
    assert controller.requested_port == expected
    assert not controller.servers and not controller.pipes


def test_direct_generation_rejects_invalid_bind_default_before_custody(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_vault import config, service

    monkeypatch.setattr(service, "run_dir", lambda: tmp_path / "run")
    monkeypatch.setattr(generation, "home_dir", lambda: tmp_path)
    monkeypatch.setenv(config.BIND_PORT_ENV, "-1")
    monkeypatch.setattr(
        config, "resolve_context",
        lambda: SimpleNamespace(port=19999, sources={"port": "default"}),
    )
    controller = generation.ServiceGeneration(
        SimpleNamespace(_runtime_can_work=True), SimpleNamespace(), [], [],
    )
    controller.generation_lease = SimpleNamespace(
        acquire=lambda: pytest.fail("invalid default must not acquire generation custody"),
    )
    with pytest.raises(ValueError, match="AGENT_VAULT_BIND_PORT"):
        asyncio.run(controller.start(None))
    assert controller.service._runtime_can_work
    assert not controller.servers and not controller.pipes


def test_retirement_releases_writer_before_nonsettling_transport_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_vault import cutover, service

    monkeypatch.setattr(service, "run_dir", lambda: tmp_path / "run")
    monkeypatch.setattr(generation, "home_dir", lambda: tmp_path)
    controller = generation.ServiceGeneration(
        SimpleNamespace(_runtime_can_work=True, _shutdown=False),
        SimpleNamespace(work=set()), [], [],
    )
    controller.writer.acquire()
    controller.has_writer = True
    controller.retiring = {"runtime_generation": "successor"}
    monkeypatch.setattr(generation, "selected_generation", lambda: "successor")
    monkeypatch.setattr(cutover, "build_auth_state", lambda _: {"fixture": True})
    sent = []

    def transfer(status: dict, payload: dict, *, writer_released: bool = False) -> bool:
        assert not controller.has_writer
        assert not controller.service._runtime_can_work
        assert not controller.connections.work
        sent.append(writer_released)
        return True

    monkeypatch.setattr(generation, "send_auth_state", transfer)
    try:
        asyncio.run(controller.tick())
        assert sent == [True]
        assert not controller.has_writer
        assert controller.service._shutdown
        assert not controller.service._runtime_can_work
        # The predecessor remains alive and its transport cleanup has not run.
        incoming = generation.ServiceGeneration(SimpleNamespace(), SimpleNamespace(), [], [])
        incoming.previous = SimpleNamespace(pid=os.getpid())
        incoming.legacy_predecessor = True
        incoming.receiver = SimpleNamespace(writer_released=lambda: True)
        assert incoming._take_writer()
        incoming.writer.release()
    finally:
        if controller.has_writer:
            controller.writer.release()


def test_ready_writer_supersedes_while_retaining_unsettled_resume_custody(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_vault import cutover, service

    monkeypatch.setattr(service, "run_dir", lambda: tmp_path / "run")
    monkeypatch.setattr(generation, "home_dir", lambda: tmp_path)
    monkeypatch.setattr(generation, "selected", lambda: False)
    monkeypatch.setattr(generation, "selected_generation", lambda: "successor")
    monkeypatch.setattr(generation, "read_endpoint", lambda _: SimpleNamespace(pid=123))
    monkeypatch.setattr(generation, "endpoint_health", lambda _: {
        "ok": True, "pid": 123, "generation_protocol": generation.PROTOCOL,
        "runtime_generation": "successor",
    })
    monkeypatch.setattr(cutover, "build_auth_state", lambda _: {"fixture": True})
    vault = service.VaultService()
    work = {object()}
    controller = generation.ServiceGeneration(vault, SimpleNamespace(work=work), [], [])
    controller.writer.acquire()
    controller.has_writer = True
    pin = object()
    recovered = False

    def recover() -> bool:
        assert controller.predecessor.custody is pin
        if recovered:
            controller.predecessor.custody = None
        return recovered

    controller.predecessor = SimpleNamespace(custody=pin, recover=recover)
    transfers = []

    def transfer(status: dict, payload: dict, *, writer_released: bool = False) -> bool:
        assert not controller.has_writer and not vault._runtime_can_work and not work
        assert controller.predecessor.custody is pin
        transfers.append(writer_released)
        return True

    monkeypatch.setattr(generation, "send_auth_state", transfer)

    async def exercise() -> None:
        nonlocal recovered
        await controller.tick()
        await controller.tick()
        assert not vault._runtime_can_work and not vault._shutdown
        assert controller.has_writer and not transfers
        assert vault.handle_request({"action": "has", "entry": "late"}) == {
            "ok": False, "error": "service_transition", "retryable": True,
        }
        work.clear()
        await controller.tick()
        assert transfers == [True] and vault._shutdown
        assert controller.predecessor.custody is pin
        incoming = generation.ServiceGeneration(SimpleNamespace(), SimpleNamespace(), [], [])
        incoming.writer.acquire()
        incoming.writer.release()
        settling = asyncio.create_task(controller.settle_retirements())
        try:
            await asyncio.sleep(.01)
            assert not settling.done()
            assert controller.predecessor.custody is pin
            recovered = True
            await asyncio.wait_for(settling, 2)
            assert controller.predecessor.custody is None
        finally:
            if not settling.done():
                settling.cancel()
                await asyncio.gather(settling, return_exceptions=True)

    try:
        asyncio.run(exercise())
    finally:
        if controller.has_writer:
            controller.writer.release()


@pytest.mark.parametrize("change", ("selection", "endpoint", "installation"))
def test_current_controller_cannot_retire_after_selection_or_discovery_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    from agent_vault import service

    monkeypatch.setattr(service, "run_dir", lambda: tmp_path)
    monkeypatch.setattr(generation, "home_dir", lambda: tmp_path)
    monkeypatch.setattr(generation, "installation_id", lambda: "fixture-installation")
    monkeypatch.setattr(generation, "selected", lambda: change != "selection")
    monkeypatch.setattr(generation, "selection_custody", lambda *_: nullcontext())
    monkeypatch.setattr(generation, "read_endpoint", lambda _: SimpleNamespace(
        pid=123 if change == "endpoint" else os.getpid(),
        installation_id="foreign" if change == "installation" else "fixture-installation",
    ))
    controller = generation.ServiceGeneration(SimpleNamespace(), SimpleNamespace(), [], [])

    def retire(current: Callable[[], bool]) -> str:
        assert not current()
        return "refused-owner-or-selection"

    controller.predecessor = SimpleNamespace(retire=retire)
    assert controller._retire_previous() == "refused-owner-or-selection"


@pytest.mark.parametrize("change", ("holder", "available", "self-writer"))
def test_older_writer_target_requires_current_contention_from_that_same_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    from agent_vault import service

    monkeypatch.setattr(service, "run_dir", lambda: tmp_path)
    monkeypatch.setattr(generation, "home_dir", lambda: tmp_path)
    monkeypatch.setattr(generation, "installation_id", lambda: "fixture-installation")
    monkeypatch.setattr(generation, "selected", lambda: True)
    monkeypatch.setattr(generation, "selection_custody", lambda *_: nullcontext())
    monkeypatch.setattr(generation, "read_endpoint", lambda _: SimpleNamespace(
        pid=os.getpid(), installation_id="fixture-installation",
    ))
    controller = generation.ServiceGeneration(SimpleNamespace(), SimpleNamespace(), [], [])
    controller.has_writer = change == "self-writer"
    events = []

    class Writer:
        def acquire(self) -> None:
            events.append("probe")
            if change == "holder":
                raise AlreadyRunningError(tmp_path / "writer.lock", 456)

        def release(self) -> None:
            events.append("release")

    def retire(current: Callable[[], bool]) -> str:
        assert not current()
        return "refused-owner-or-selection"

    controller.writer = Writer()
    target = SimpleNamespace(pid=123, retire=retire)
    assert controller._retire_previous(target, True) == "refused-owner-or-selection"
    assert events == {
        "holder": ["probe"], "available": ["probe", "release"], "self-writer": [],
    }[change]


def test_confirmed_predecessor_exit_does_not_repeat_native_process_probes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_vault import service

    monkeypatch.setattr(service, "run_dir", lambda: tmp_path)
    monkeypatch.setattr(generation, "home_dir", lambda: tmp_path)
    monkeypatch.setattr(generation, "selected", lambda: True)
    monkeypatch.setattr(generation, "read_endpoint", lambda _: SimpleNamespace(pid=os.getpid()))
    controller = generation.ServiceGeneration(SimpleNamespace(), SimpleNamespace(), [], [])
    controller.previous = SimpleNamespace(pid=123)
    controller.predecessor_retirement = "exited"
    controller.has_writer = controller.hooks_started = controller.aliases_after_exit = True

    def unexpected(_: int) -> bool:
        pytest.fail("A positively settled predecessor was probed again")

    monkeypatch.setattr(generation, "pid_alive", unexpected)
    for _ in range(2):
        asyncio.run(controller.tick())


@pytest.mark.parametrize("condition", ("stale", "live", "replaced", "owner-live"))
def test_unix_alias_reclaims_only_the_captured_dead_listener(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, condition: str,
) -> None:
    legacy = str(tmp_path / "legacy.sock")
    state = {"identity": (1, 2), "exists": True}
    events = []
    controller = object.__new__(generation.ServiceGeneration)
    controller.socket = str(tmp_path / "incoming.sock")
    controller.pipe = None
    controller.port = controller.requested_port = 0
    controller.servers = []
    controller.unix_alias = SimpleNamespace(
        dead_identity=lambda: None if condition == "owner-live" else (1, 2),
    )
    controller.connections = SimpleNamespace(callback=lambda _: object())

    def stale(path: str) -> bool:
        assert path == legacy
        if condition == "replaced":
            state["identity"] = (3, 4)
        return condition != "live"

    controller.api = SimpleNamespace(SOCKET_PATH=legacy)
    monkeypatch.setattr(generation, "socket_identity", lambda _: state["identity"])
    monkeypatch.setattr(controller, "_record_socket", lambda _: None)
    monkeypatch.setattr(controller, "_unix_stale", stale)
    monkeypatch.setattr(generation.os.path, "lexists", lambda _: state["exists"])

    def unlink(path: str) -> None:
        assert path == legacy and state["identity"] == (1, 2)
        state["exists"] = False
        events.append("unlink")

    async def listen(callback, *, path):
        assert path == legacy and not state["exists"]
        events.append("listen")
        return object()

    monkeypatch.setattr(generation.os, "unlink", unlink)
    monkeypatch.setattr(generation.os, "chmod", lambda *args: None)
    monkeypatch.setattr(generation.asyncio, "start_unix_server", listen, raising=False)
    asyncio.run(controller._bind_legacy_aliases())
    assert events == (["unlink", "listen"] if condition == "stale" else [])
    assert len(controller.servers) == (1 if condition == "stale" else 0)


@pytest.mark.parametrize("error,expected", (
    (None, False), (errno.ECONNREFUSED, True), (errno.ENOENT, True),
    (errno.EACCES, False), (errno.ETIMEDOUT, False),
))
def test_unix_stale_probe_does_not_confuse_denied_or_unknown_with_no_listener(
    monkeypatch: pytest.MonkeyPatch, error: int | None, expected: bool,
) -> None:
    class Peer:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def settimeout(self, value: float) -> None:
            assert value == .25

        def connect(self, path: str) -> None:
            assert path == "fixture.sock"
            if error is not None:
                raise OSError(error, "fixture probe")

    monkeypatch.setattr(generation.socket, "AF_UNIX", object(), raising=False)
    monkeypatch.setattr(generation.socket, "socket", lambda *args: Peer())
    assert generation.ServiceGeneration._unix_stale("fixture.sock") is expected


@pytest.mark.parametrize("receipt,version", (
    (True, 1), (None, 1), ("true", 1), (1, 1), (True, True), (True, 99),
))
def test_only_valid_native_handoff_records_writer_release(
    monkeypatch: pytest.MonkeyPatch, receipt: object, version: object,
) -> None:
    from agent_vault import service_handoff as handoff
    from agent_vault.cutover import AUTH_STATE_CONTRACT

    receiver = object.__new__(handoff.AuthReceiver)
    receiver.peer = object()
    receiver.generation = "fixture-generation"
    receiver.cancelled = threading.Event()
    receiver._lock = threading.Lock()
    receiver._payload = None
    receiver._writer_released = False
    receiver.listener = SimpleNamespace(accept=lambda *args, **kwargs: SimpleNamespace(close=lambda: None))
    frame = dict(
        contract=AUTH_STATE_CONTRACT, version=version, target_generation="fixture-generation",
        auth={"contract": AUTH_STATE_CONTRACT, "version": version},
    )
    if receipt is not None:
        frame["credential_writer_released"] = receipt
    monkeypatch.setattr(handoff, "read_frame", lambda *args: frame)
    monkeypatch.setattr(handoff, "write_frame", lambda *args: None)
    receiver._receive()
    assert receiver.writer_released() is (receipt is True and type(version) is int and version == 1)


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


@pytest.mark.parametrize("nested", (False, True), ids=("direct", "application"))
@pytest.mark.parametrize("marker", (False, True), ids=("slot-name", "build-generation"))
def test_imported_generation_preserves_installed_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, nested: bool, marker: bool,
) -> None:
    import importlib.util

    root = tmp_path / "vault"
    first = root / "versions" / "1.0.0.source.first"
    second = root / "versions" / "1.0.0.source.second"
    for slot, identity in ((first, "a" * 32), (second, "b" * 32)):
        (slot / ".venv").mkdir(parents=True)
        if marker:
            (slot / ".runtime-bootstrap").mkdir()
            (slot / ".runtime-bootstrap" / "runtime-generation").write_text(identity)
    monkeypatch.setattr(sys, "prefix", str(first / ".venv" if nested else first))
    spec = importlib.util.spec_from_file_location(
        "agent_vault._fixture_generation", generation.__file__,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "home_dir", lambda: root)
    (root / "current-version").write_text(first.name)
    expected = "a" * 32 if marker else first.name
    own_health = {
        "ok": True, "pid": 123, "generation_protocol": module.PROTOCOL,
        "runtime_generation": expected,
    }
    assert module.selected()
    assert module.same_generation(own_health)
    assert not module.same_generation(own_health | {"runtime_generation": "1.0.0"})
    (root / "current-version").write_text(second.name)
    assert not module.selected()
    assert not module.same_generation(own_health | {
        "runtime_generation": "b" * 32 if marker else second.name,
    })


@pytest.mark.parametrize("nested", (False, True), ids=("direct", "application"))
def test_real_venv_generation_obeys_reselection(tmp_path: Path, nested: bool) -> None:
    import venv

    root = tmp_path / "vault"
    slot = root / "versions" / "1.0.0.source.fixture"
    prefix = slot / ".venv" if nested else slot
    venv.EnvBuilder(with_pip=False).create(prefix)
    (slot / ".runtime-bootstrap").mkdir()
    (slot / ".runtime-bootstrap" / "runtime-generation").write_text("a" * 32)
    (root / "current-version").write_text(slot.name)
    python = prefix / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    result = subprocess.run(
        [
            str(python), "-I", "-B", "-c",
            "import json,sys\n"
            "from pathlib import Path\n"
            "sys.path[:0]=json.loads(sys.argv[1])\n"
            "from agent_vault import service_generation as g\n"
            "root=Path(sys.argv[2])\n"
            "g.home_dir=lambda:root\n"
            "before=g.selected()\n"
            "(root/'current-version').write_text('1.0.0.source.successor')\n"
            "print(json.dumps([g.PROCESS_GENERATION,before,g.selected()]))\n",
            json.dumps(sys.path), str(root),
        ],
        capture_output=True, text=True, timeout=20, check=False,
        **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["a" * 32, True, False]


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
    controller.predecessor = controller.writer_predecessor = None
    controller.retiring = {"runtime_generation": "displaced-successor"}
    monkeypatch.setattr(generation, "selected_generation", lambda: "next-successor")
    asyncio.run(controller.tick())
    assert not controller.service._runtime_can_work
    assert not controller.service._shutdown
    controller.connections.work.clear()
    asyncio.run(controller.tick())
    assert controller.service._shutdown
    assert not controller.service._runtime_can_work


@pytest.mark.parametrize("transport", ("tcp", "unix", "pipe"))
def test_retirement_settles_admitted_queued_dispatch_but_rejects_late_arrivals(
    monkeypatch: pytest.MonkeyPatch, transport: str,
) -> None:
    from agent_vault.service import VaultService, handle_client
    from agent_vault.service_connections import VaultConnections

    vault = VaultService()
    dispatched = []
    ttl_checks = []
    monkeypatch.setattr(vault.cli, "has_password", lambda db=None: False)
    monkeypatch.setattr(vault, "_check_password_ttl", lambda: ttl_checks.append("checked"))

    def has(kpdb: str, entry: str, group: str | None = None) -> bool:
        assert ttl_checks, "admitted credential work skipped expiry reconciliation"
        dispatched.append((kpdb, entry, group, vault._request_ctx.allow_prompt,
                           vault._request_ctx.reason))
        return True

    monkeypatch.setattr(vault, "has", has)
    connections = VaultConnections(vault, handle_client)
    controller = object.__new__(generation.ServiceGeneration)
    controller.service = vault
    controller.connections = connections
    controller.predecessor = controller.writer_predecessor = None
    controller.retiring = {"runtime_generation": "displaced-successor"}
    monkeypatch.setattr(generation, "selected_generation", lambda: "next-successor")

    async def exercise() -> None:
        loop = asyncio.get_running_loop()
        queued = []

        def enqueue(executor: object, callback: Callable, *args: object) -> asyncio.Future:
            future = loop.create_future()
            queued.append((callback, args, future))
            return future

        monkeypatch.setattr(loop, "run_in_executor", enqueue)
        writer = object()
        request = {"action": "has", "kpdb": "fixture.kdbx", "entry": "fixture",
                   "group": "fixture-group", "allow_prompt": False}
        pending = connections.request(writer, request, "fixture-peer", transport)
        assert pending in connections.work and pending in connections.workers
        assert writer in connections.busy
        assert len(queued) == 1 and not dispatched and not ttl_checks

        await controller.tick()
        assert not vault._runtime_can_work and not vault._shutdown
        assert pending in connections.work and not pending.done()
        late_writer = object()
        late = connections.request(late_writer, request, "late-peer", transport)
        assert await late == {"ok": False, "error": "service_transition", "retryable": True}
        assert len(queued) == 1 and late_writer not in connections.busy
        assert connections.work == connections.workers == {pending}

        callback, args, future = queued.pop()
        future.set_result(callback(*args))
        assert await pending == {"ok": True, "exists": True}
        assert dispatched == [("fixture.kdbx", "fixture", "fixture-group", False,
                               "action=has peer=fixture-peer")]
        await asyncio.sleep(0)
        assert not connections.work and not connections.workers
        await controller.tick()
        assert vault._shutdown and not vault._runtime_can_work

    asyncio.run(exercise())


@pytest.mark.parametrize("metadata", (
    {"admitted": True}, {"_admitted": True}, {"_admission_ticket": {"accepted": True}},
    {"health_only": True},
))
def test_public_request_cannot_forge_retired_lifecycle_admission(
    monkeypatch: pytest.MonkeyPatch, metadata: dict,
) -> None:
    from agent_vault.service import VaultService

    vault = VaultService()
    vault._runtime_can_work = False

    def unexpected_dispatch(*args: object) -> dict:
        pytest.fail("Client metadata bypassed the public lifecycle guard")

    monkeypatch.setattr(vault, "_handle_admitted_request", unexpected_dispatch)
    assert vault.handle_request({"action": "has", **metadata}) == {
        "ok": False, "error": "service_transition", "retryable": True,
    }


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
    controller.predecessor = controller.writer_predecessor = None
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


@pytest.mark.parametrize("accepting", (False, True))
def test_native_generation_ping_does_not_resolve_configuration_or_backend(
    monkeypatch: pytest.MonkeyPatch, accepting: bool,
) -> None:
    from agent_vault import service
    from agent_vault.rendezvous import Endpoint
    from agent_vault.service_connections import VaultConnections

    vault = service.VaultService()
    vault._runtime_can_work = accepting

    def unavailable(*args, **kwargs):
        pytest.fail("Native generation health entered configuration or backend work")

    monkeypatch.setattr(service, "resolve_kpdb", unavailable)
    monkeypatch.setattr(vault, "_check_password_ttl", unavailable)
    monkeypatch.setattr(vault.cli, "status", unavailable)

    async def exercise() -> None:
        connections = VaultConnections(vault, service.handle_client)
        server = await asyncio.start_server(
            connections.callback("tcp"), "127.0.0.1", 0,
        )
        endpoint = Endpoint(
            "tcp", f"127.0.0.1:{server.sockets[0].getsockname()[1]}",
            pid=os.getpid(), installation_id=generation.installation_id(),
        )
        try:
            response = await asyncio.to_thread(generation.endpoint_health, endpoint)
            assert generation.valid_generation_health(response)
            assert response["pid"] == os.getpid()
            assert response["accepting_work"] is accepting
            assert "cli" not in response
        finally:
            await connections.close([server], [], lambda: None)

    asyncio.run(exercise())


def test_connections_close_does_not_wait_forever_on_stalled_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_vault.service_connections import VaultConnections

    class StalledServer:
        def close(self) -> None:
            pass

        async def wait_closed(self) -> None:
            await asyncio.Event().wait()

    vault = SimpleNamespace(_shutdown=False)
    connections = VaultConnections(vault, None)
    monkeypatch.setattr(VaultConnections, "SERVER_CLOSE_GRACE", .05)
    cleaned = []

    async def exercise() -> None:
        await asyncio.wait_for(
            connections.close([StalledServer()], [], lambda: cleaned.append(True)), 5,
        )

    asyncio.run(exercise())
    assert cleaned == [True]


def test_connections_close_still_retries_and_reports_server_close_errors() -> None:
    from agent_vault.service_connections import VaultConnections

    class FlakyServer:
        attempts = 0

        def close(self) -> None:
            pass

        async def wait_closed(self) -> None:
            self.attempts += 1
            if self.attempts == 1:
                raise OSError("fixture close failure")

    server = FlakyServer()
    connections = VaultConnections(SimpleNamespace(_shutdown=False), None)
    cleaned = []
    with pytest.raises(OSError, match="fixture close failure"):
        asyncio.run(connections.close([server], [], lambda: cleaned.append(True)))
    assert server.attempts == 2
    assert cleaned == [True]


def test_native_generation_ping_accepts_legacy_full_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_vault import service
    from agent_vault.rendezvous import Endpoint

    response = {
        "ok": True, "pid": 123, "generation_protocol": generation.PROTOCOL,
        "runtime_generation": "fixture-generation", "accepting_work": True,
        "cli": "locked",
    }

    def legacy_ping(request, host, port, timeout):
        assert request["action"] == "ping"
        assert timeout == .25
        return response

    monkeypatch.setattr(service, "_dial_tcp", legacy_ping)
    endpoint = Endpoint(
        "tcp", "127.0.0.1:12345", pid=123,
        installation_id=generation.installation_id(),
    )
    assert generation.endpoint_health(endpoint) is response


def test_contextual_ping_retains_backend_status_and_expiration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_vault import service

    calls = []
    vault = service.VaultService()
    monkeypatch.setattr(vault, "_check_password_ttl", lambda: calls.append("expiry"))
    monkeypatch.setattr(service, "resolve_kpdb", lambda **kwargs: "fixture.kdbx")
    monkeypatch.setattr(
        vault.cli, "status", lambda kpdb: calls.append(kpdb) or "locked",
    )
    response = vault.handle_request({"action": "ping", "kpdb": "fixture.kdbx"})
    assert response["cli"] == "locked"
    assert response["runtime_generation"] == service.PROCESS_GENERATION
    assert calls == ["expiry", "fixture.kdbx"]


@pytest.mark.parametrize("protected_cache", [False, True])
def test_legacy_ping_uses_the_resident_default_without_an_owner_lookup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, protected_cache: bool,
) -> None:
    from agent_vault import extensions, service

    monkeypatch.setenv("AGENT_VAULT_CACHE", "1" if protected_cache else "0")
    monkeypatch.delenv("AGENT_VAULT_CACHE_DIR", raising=False)
    if protected_cache:
        monkeypatch.setenv("AGENT_VAULT_CACHE_DIR", str(tmp_path / "cache"))
    registry = extensions.ExtensionRegistry()
    registry._loaded = True
    monkeypatch.setattr(extensions, "_REGISTRY", registry)
    vault = service.VaultService()
    database = tmp_path / "resident.kdbx"
    database.touch()
    monkeypatch.setattr(service, "resolve_kpdb", lambda **kwargs: str(database))
    vault.initialize()

    def unavailable(**kwargs):
        pytest.fail("Legacy generation ping repeated the configuration-owner lookup")

    monkeypatch.setattr(service, "resolve_kpdb", unavailable)
    configuration_calls: list[str] = []

    def configuration_source(*_args: object, **_kwargs: object) -> dict[str, str]:
        configuration_calls.append("called")
        return {"group": "unexpected"}

    registry.register_config_source(configuration_source)
    monkeypatch.setattr(
        vault.cli, "status",
        lambda kpdb: "locked" if kpdb == str(database) else "wrong-vault",
    )
    response = vault.handle_request({"action": "ping"})
    assert response["cli"] == "locked"
    assert response["runtime_generation"] == service.PROCESS_GENERATION
    assert configuration_calls == []
    selections = iter(("first.kdbx", "second.kdbx"))
    monkeypatch.setattr(service, "resolve_kpdb", lambda **kwargs: next(selections))
    selected = []
    monkeypatch.setattr(
        vault, "has", lambda kpdb, *args: selected.append(kpdb) or True,
    )
    for _ in range(2):
        assert vault.handle_request({"action": "has", "entry": "fixture"})["exists"]
    assert selected == ["first.kdbx", "second.kdbx"]


CHILD = r"""
import asyncio, hashlib, json, os, subprocess, sys, time
from pathlib import Path
from types import SimpleNamespace

root, identity, port, mode = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), sys.argv[4]
if mode in {"hung-cleanup", "hung-work", "bounded-successor", "prepared-successor", "rollback-successor", "resume-failure-successor", "retirement-probe-successor", "passive-legacy", "backend-work"}:
    sys.path.insert(0, str(root / "versions" / identity))
os.environ.update(
    AGENT_VAULT_HOME=str(root), AGENT_VAULT_RUN_DIR=str(root / "run"),
    AGENT_VAULT_PID=str(root / "vault.pid"), AGENT_VAULT_SOCKET=str(root / "vault.sock"),
    AGENT_VAULT_PIPE=r"\\.\pipe\vault-generation-" + hashlib.sha256(str(root).encode()).hexdigest()[:20],
    AGENT_VAULT_LOG=str(root / (identity + ".log")), AGENT_VAULT_PORT=str(port),
)
os.environ.pop("AGENT_VAULT_INSTALLATION_ID", None)
from agent_vault import service, service_generation as generation
from agent_vault import service_handoff
from agent_vault import service_predecessor
if mode in {"bounded-successor", "prepared-successor", "rollback-successor", "resume-failure-successor", "retirement-probe-successor"}:
    service_predecessor.GRACE_SECONDS = 3
if mode == "retirement-probe-successor":
    census = service_predecessor._has_children
    def held_retirement(pid):
        (root / "pin-owner.retirement").touch()
        return census(pid)
    service_predecessor._has_children = held_retirement
if mode == "resume-failure-successor":
    from agent_procutil.process_retirement import RetirementRefused
    from agent_procutil.retirement_custody import RetirementCustody, _Pin
    close = RetirementCustody.close
    def held_resume(self):
        if not (root / "resume.release").exists():
            from agent_procutil import _daemon_instance_store as store
            (root / (identity + ".custodian")).write_text(json.dumps({
                "pid": self._process.pid,
                "job": store._expiry_job.name if os.name == "nt" else None,
            }))
            raise RetirementRefused("Owned fixture lifeline close is held")
        return close(self)
    RetirementCustody.close = held_resume
    def held_pin_close(self, *_):
        raise RetirementRefused("Owned fixture pin close is held")
    _Pin.__exit__ = held_pin_close
    service_predecessor._has_children = lambda _: True
    settle = generation.ServiceGeneration.settle_retirements
    async def observed_settlement(self):
        (root / "resume.shutdown").touch()
        await settle(self)
    generation.ServiceGeneration.settle_retirements = observed_settlement
if mode == "rollback-successor":
    from agent_procutil.retirement_custody import _Pin
    terminate = _Pin.terminate
    def held_terminate(self, *args, **kwargs):
        (root / "retire.ready").touch()
        deadline = time.monotonic() + 10
        while not (root / "retire.continue").exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("Owned retirement fixture was not released")
            time.sleep(.01)
        return terminate(self, *args, **kwargs)
    _Pin.terminate = held_terminate
if mode == "legacy-receiver":
    read = service_handoff.read_frame
    def legacy_read(*args):
        frame = read(*args)
        frame.pop("credential_writer_released", None)
        return frame
    service_handoff.read_frame = legacy_read
if mode == "legacy-sender":
    send = generation.send_auth_state
    def legacy_send(status, payload, **kwargs):
        return send(status, payload)
    generation.send_auth_state = legacy_send
if mode == "legacy-health":
    def legacy_dial(dial):
        def send(request, *args, **kwargs):
            request = dict(request)
            request.pop("health_only", None)
            (root / "legacy-health.json").write_text(json.dumps({
                "transport": dial.__name__, "request": request,
            }))
            return dial(request, *args, **kwargs)
        return send
    service._dial_tcp = legacy_dial(service._dial_tcp)
    service._dial_unix = legacy_dial(service._dial_unix)
generation.PROCESS_GENERATION = service.PROCESS_GENERATION = identity
generation.PROCESS_SLOT = root / "versions" / identity
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
if mode == "backend-work":
    from agent_vault import keepassxc
    backend_program = r'''
import os, sys, time
from pathlib import Path
root = Path(sys.argv[1])
sys.stdin.read()
(root / "backend.pid").write_text(str(os.getpid()))
(root / "backend.entered").touch()
deadline = time.monotonic() + 8
while not (root / "backend.release").exists():
    if time.monotonic() >= deadline:
        (root / "backend.expired").touch()
        raise RuntimeError("Owned backend fixture was not released")
    time.sleep(.01)
(root / "backend.finished").touch()
'''
    run_backend = subprocess.run
    keepassxc.subprocess = SimpleNamespace(run=lambda argv, **kwargs: run_backend(
        [sys.executable, "-c", backend_program, str(root)], **kwargs,
    ))
    vault.cli._cli_path = sys.executable

def native(kpdb, entry, group=None):
    with (root / "work.jsonl").open("a") as stream:
        stream.write(json.dumps([identity, "entered"]) + "\n")
    (root / (identity + ".entered")).touch()
    if mode == "backend-work":
        assert vault.cli.edit_password(kpdb, entry, "fixture-value")[0]
    else:
        deadline = time.monotonic() + 15
        while not (root / (identity + ".release")).exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("Owned fixture work was not released")
            time.sleep(.01)
    with (root / "work.jsonl").open("a") as stream:
        stream.write(json.dumps([identity, "settled"]) + "\n")
    return True

vault.has = native
if mode == "hung-cleanup":
    from agent_vault.service_connections import VaultConnections
    close = VaultConnections.close
    async def held_close(self, *args):
        (root / "cleanup.entered").touch()
        while not (root / (identity + ".stop")).exists():
            await asyncio.sleep(.02)
        return await close(self, *args)
    VaultConnections.close = held_close
if mode == "fail":
    async def fail(*args, **kwargs):
        raise OSError("fixture listener construction failed")
    asyncio.start_server = fail
async def main():
    if mode == "prepared-successor":
        (root / (identity + ".prepared")).touch()
        deadline = time.monotonic() + 15
        while not (root / (identity + ".start")).exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("Owned successor fixture was not started")
            await asyncio.sleep(.02)
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
    endpoint = generation.read_endpoint(root / "run")
    return endpoint.to_record() if endpoint is not None else None


def _target(endpoint: dict) -> int | str:
    if endpoint["transport"] == "unix":
        return endpoint["endpoint"]
    candidates = [endpoint, *endpoint.get("alt", [])]
    return next(
        int(item["endpoint"].rsplit(":", 1)[1])
        for item in candidates if item["transport"] == "tcp"
    )


def _send(target: int | str, request: dict) -> dict:
    if isinstance(target, str):
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            connection.settimeout(1)
            connection.connect(target)
        except BaseException:
            connection.close()
            raise
    else:
        connection = socket.create_connection(("127.0.0.1", target), timeout=1)
    with connection:
        connection.settimeout(10)
        connection.sendall((json.dumps(request) + "\n").encode())
        with connection.makefile("rb") as stream:
            return json.loads(stream.readline())


def _serving(port: int | str, pid: int) -> bool:
    try:
        return _send(port, {"action": "ping"}).get("pid") == pid
    except (OSError, ValueError):
        return False


def test_unix_only_discovery_uses_the_advertised_native_transport(monkeypatch):
    events = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def settimeout(self, value):
            pass

        def connect(self, target):
            events.append(target)

        def sendall(self, payload):
            assert json.loads(payload)["action"] == "ping"

        def makefile(self, mode):
            from io import BytesIO
            return BytesIO(b'{"ok":true,"pid":123}\n')

    monkeypatch.setattr(socket, "AF_UNIX", object(), raising=False)
    monkeypatch.setattr(socket, "socket", lambda family, kind: Connection())
    target = _target({"transport": "unix", "endpoint": "fixture.sock"})
    assert _send(target, {"action": "ping"}) == {"ok": True, "pid": 123}
    assert events == ["fixture.sock"]


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
    from agent_procutil.daemon_instances import DaemonInstanceSpec, spawn_instance

    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("AGENT_VAULT_", "VAULT_")) and key not in {"KPDB", "KEE_PASS_DB"}
    }
    source = (
        root / "versions" / identity / "agent_vault" / "service.py"
        if mode in {
            "hung-cleanup", "hung-work", "bounded-successor", "prepared-successor",
            "rollback-successor", "resume-failure-successor",
            "retirement-probe-successor",
            "passive-legacy", "backend-work",
        }
        else Path(generation.__file__).with_name("service.py")
    )
    return spawn_instance(
        [sys.executable, "-c", CHILD, str(root), identity, str(port), mode],
        instance=DaemonInstanceSpec("agent-vault", root, source, purpose="unattended-test"),
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env, **no_window_kwargs(),
    )


def _settle(root: Path, processes: list[subprocess.Popen]) -> list[str]:
    (root / "backend.release").touch()
    (root / "retire.continue").touch()
    (root / "resume.release").touch()
    for identity in ("newer", "middle", "older"):
        (root / (identity + ".start")).touch()
        (root / (identity + ".release")).touch()
        (root / (identity + ".stop")).touch()
    outputs = ["".join(process.communicate(timeout=10)) for process in processes]
    for output in outputs:
        if output:
            print("Owned generation output:", output[-8192:])
    for path in root.glob("*.log"):
        print(path.name, path.read_text(encoding="utf-8", errors="replace")[-8192:])
    print("Owned backend fixture expired:", (root / "backend.expired").exists())
    return outputs


def _stage_generation(root: Path, identity: str) -> None:
    slot = root / "versions" / identity
    shutil.copytree(
        Path(generation.__file__).parent, slot / "agent_vault",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir()
    shutil.copyfile(
        Path(__file__).parent.parent / "scripts" / "versioned_runtime_leases.py",
        bootstrap / "versioned_runtime_leases.py",
    )


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


def _retain_fixture_job(name: str):
    import ctypes
    from ctypes import wintypes
    from agent_procutil import JobHandle, _kernel32

    kernel = _kernel32()
    kernel.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.OpenJobObjectW.restype = wintypes.HANDLE
    handle = kernel.OpenJobObjectW(0x0004, False, name)
    assert handle, ctypes.get_last_error()
    return JobHandle(handle, name=name)


@pytest.mark.skipif(os.name != "nt" and sys.platform != "linux", reason="Native process references")
@pytest.mark.parametrize("old_mode,incoming_mode,warm", (
    ("legacy-health", "normal", True), ("normal", "no-overlap", False),
    ("legacy-sender", "normal", True), ("normal", "legacy-receiver", True),
))
def test_real_successor_is_ready_before_self_retirement_and_cannot_overlap_writers(
    tmp_path: Path, old_mode: str, incoming_mode: str, warm: bool,
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from zdd.private_protocol import Deadline
    from zdd.diagnostics import process_start_time

    (tmp_path / "current-version").write_text("newer")
    durable = tmp_path / "fixture.kdbx"
    durable.write_bytes(b"fixture durable bytes")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        fixed_port = reservation.getsockname()[1]
    old = _start(tmp_path, "newer", fixed_port, old_mode)
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
            (tmp_path / "current-version").write_text("older")
            new = _start(tmp_path, "older", fixed_port, incoming_mode)
            children.append(new)
            _until(lambda: (_endpoint(tmp_path) or {}).get("pid") not in {None, old_pid}, children)
            endpoint = _endpoint(tmp_path)
            new_pid = endpoint["pid"]
            assert process_start_time(new_pid)
            new_port = _target(endpoint)
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
        if old_mode == "legacy-health":
            assert json.loads((tmp_path / "legacy-health.json").read_text()) == {
                "transport": "_dial_tcp" if os.name == "nt" else "_dial_unix",
                "request": {"action": "ping"},
            }
        _until(lambda: _send(new_port, {"action": "ping"})["accepting_work"], [new])
        assert _send(new_port, {"action": "ping"})["unlocked_vaults"] == (
            [str(durable)] if warm else []
        )
        assert _endpoint(tmp_path)["pid"] == new_pid
        assert int((tmp_path / "vault.pid").read_text()) == new_pid
        (tmp_path / "older.release").touch()
        assert _send(new_port, {"action": "has", "kpdb": str(durable), "entry": "fixture"})["ok"]
        _until(lambda: _serving(fixed_port, new_pid), [new])
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


@pytest.mark.skipif(os.name != "nt" and sys.platform != "linux", reason="Native pinned retirement")
def test_native_rollback_waits_for_pinned_retirement_to_confirm_exit(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from agent_procutil._daemon_instance_identity import birth_identity

    for identity in ("newer", "older"):
        _stage_generation(tmp_path, identity)
    (tmp_path / "current-version").write_text("newer")
    old = _start(tmp_path, "newer", 0, "hung-work")
    children = [old]
    rollback = r"""
import importlib.util, sys
from contextlib import contextmanager
from pathlib import Path
from agent_procutil._daemon_instance_identity import identity_state
root, sdk = Path(sys.argv[1]), Path(sys.argv[2])
pid, birth = int(sys.argv[3]), sys.argv[4]
spec = importlib.util.spec_from_file_location("rollback_sdk", sdk)
runtime = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runtime
exec(compile(sdk.read_bytes(), str(sdk), "exec"), runtime.__dict__)
original = runtime._publication_lease
@contextmanager
def observed(root):
    try:
        with original(root, timeout=0):
            raise AssertionError("Retirement did not hold SDK selector custody")
    except runtime.RetentionTimeout:
        (root / "rollback.blocked").touch()
    with original(root, timeout=8):
        assert identity_state(pid, birth) in {"dead", "reused"}
        (root / "rollback.acquired").touch()
        yield
runtime._publication_lease = observed
runtime.activate(root, "newer", link_free=True)
assert (root / "current-version").read_text().strip() == "newer"
"""
    with ThreadPoolExecutor(max_workers=2) as worker:
        try:
            _until(lambda: _endpoint(tmp_path) is not None, children)
            endpoint = _endpoint(tmp_path)
            pid = endpoint["pid"]
            birth = birth_identity(pid)
            pending = worker.submit(
                _send, _target(endpoint),
                {"action": "has", "kpdb": str(tmp_path / "fixture.kdbx"), "entry": "fixture"},
            )
            _until(lambda: (tmp_path / "newer.entered").exists(), children)
            (tmp_path / "current-version").write_text("older")
            new = _start(tmp_path, "older", 0, "rollback-successor")
            children.append(new)
            _until(lambda: (tmp_path / "retire.ready").exists(), children)
            competing = worker.submit(
                subprocess.run,
                [sys.executable, "-c", rollback, str(tmp_path),
                 str(Path(__file__).parent.parent / "scripts" / "versioned_runtime.py"),
                 str(pid), birth],
                capture_output=True, text=True, timeout=12, **no_window_kwargs(),
            )
            _until(lambda: (tmp_path / "rollback.blocked").exists(), [new])
            assert old.poll() is None
            assert not (tmp_path / "rollback.acquired").exists()
            (tmp_path / "retire.continue").touch()
            result = competing.result(timeout=12)
            assert result.returncode == 0, result.stdout + result.stderr
            assert (tmp_path / "rollback.acquired").exists()
            old.communicate(timeout=2)
            assert old.returncode != 0
            with pytest.raises((ValueError, OSError)):
                pending.result(timeout=2)
        finally:
            _settle(tmp_path, children)


@pytest.mark.skipif(os.name != "nt" and sys.platform != "linux", reason="Native pinned recovery")
def test_native_later_successor_refuses_a_live_custodian_child(tmp_path: Path) -> None:
    for identity in ("newer", "middle", "older"):
        _stage_generation(tmp_path, identity)
    (tmp_path / "current-version").write_text("newer")
    old = _start(tmp_path, "newer", 0, "hung-cleanup")
    children = [old]
    try:
        _until(lambda: _endpoint(tmp_path) is not None, children)
        (tmp_path / "current-version").write_text("middle")
        middle = _start(tmp_path, "middle", 0, "resume-failure-successor")
        children.append(middle)
        _until(lambda: (_endpoint(tmp_path) or {}).get("pid") == middle.pid, children)
        target = _target(_endpoint(tmp_path))
        _until(
            lambda: (
                (status := _send(target, {"action": "ping"}))["accepting_work"]
                and status["predecessor_retirement"] == "refused-native-resume"
            ), children,
        )
        (tmp_path / "current-version").write_text("older")
        incoming = _start(tmp_path, "older", 0, "retirement-probe-successor")
        children.append(incoming)
        _until(lambda: (_endpoint(tmp_path) or {}).get("pid") == incoming.pid, children)
        target = _target(_endpoint(tmp_path))
        _until(lambda: (tmp_path / "resume.shutdown").exists(), children)
        _until(
            lambda: _send(target, {"action": "ping"})["predecessor_retirement"] != "grace",
            children,
        )
        status = _send(target, {"action": "ping"})
        assert status["accepting_work"]
        assert status["predecessor_retirement"] == "refused-live-workers"
        assert (tmp_path / "pin-owner.retirement").exists()
        assert middle.poll() is None and old.poll() is None
        (tmp_path / "resume.release").touch()
        middle.communicate(timeout=6)
        assert middle.returncode == 0
        (tmp_path / "newer.stop").touch()
        old.communicate(timeout=3)
        assert old.returncode == 0
    finally:
        _settle(tmp_path, children)


@pytest.mark.skipif(os.name != "nt" and sys.platform != "linux", reason="Native pinned recovery")
@pytest.mark.parametrize("caller_dies", (False, True), ids=("shutdown", "caller-death"))
def test_native_resume_pin_survives_reselection_and_shutdown_until_settled(
    tmp_path: Path, caller_dies: bool,
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from zdd.private_protocol import Deadline

    for identity in ("newer", "older"):
        _stage_generation(tmp_path, identity)
    (tmp_path / "current-version").write_text("newer")
    old = _start(tmp_path, "newer", 0, "hung-work")
    children = [old]
    fixture_job = custodian = None
    with ThreadPoolExecutor(max_workers=1) as worker:
        try:
            _until(lambda: _endpoint(tmp_path) is not None, children)
            first = _endpoint(tmp_path)
            pending = worker.submit(
                _send, _target(first),
                {"action": "has", "kpdb": str(tmp_path / "fixture.kdbx"), "entry": "fixture"},
            )
            _until(lambda: (tmp_path / "newer.entered").exists(), children)
            (tmp_path / "current-version").write_text("older")
            new = _start(tmp_path, "older", 0, "resume-failure-successor")
            children.append(new)
            _until(lambda: (_endpoint(tmp_path) or {}).get("pid") not in {None, first["pid"]}, children)
            incoming = _endpoint(tmp_path)
            target = _target(incoming)
            _until(
                lambda: _send(target, {"action": "ping"})["predecessor_retirement"] == "refused-native-resume",
                children,
            )
            assert not _send(target, {"action": "ping"})["accepting_work"]
            assert old.poll() is None and not pending.done()
            if caller_dies:
                offer = json.loads((tmp_path / "older.custodian").read_text())
                custodian = _capture_process(offer["pid"])
                if os.name == "nt":
                    # Preserve test containment while isolating caller death from Job teardown.
                    fixture_job = _retain_fixture_job(offer["job"])
                # Only this fixture-owned caller dies; its worker keeps the original pin.
                new.kill()
                new.communicate(timeout=3)
                assert new.returncode != 0
                assert not (tmp_path / "resume.release").exists()
            else:
                (tmp_path / "current-version").write_text("newer")
                (tmp_path / "older.stop").touch()
                _until(lambda: (tmp_path / "resume.shutdown").exists(), children)
                assert new.poll() is None
                assert old.poll() is None and not pending.done()
                (tmp_path / "resume.release").touch()
                new.communicate(timeout=6)
                assert new.returncode == 0
            assert old.poll() is None
            (tmp_path / "newer.release").touch()
            assert pending.result(timeout=3)["ok"]
            assert ["newer", "settled"] in [
                json.loads(line) for line in (tmp_path / "work.jsonl").read_text().splitlines()
            ]
            if custodian is not None:
                custodian.wait_exit(Deadline(3))
        finally:
            try:
                _settle(tmp_path, children)
            finally:
                if custodian is not None:
                    custodian.close()
                if fixture_job is not None:
                    fixture_job.close()
    assert old.returncode == 0


@pytest.mark.parametrize("layout", (
    "missing", "foreign-slot", "directory-helper", "corrupt", "truncated", "busy",
))
def test_missing_or_unattributable_sdk_cannot_retire_a_predecessor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, layout: str,
) -> None:
    from agent_vault import service

    slot = tmp_path / "versions" / "incoming"
    bootstrap = slot / ".runtime-bootstrap"
    if layout == "busy":
        _stage_generation(tmp_path, "incoming")
    else:
        bootstrap.mkdir(parents=True)
    if layout == "directory-helper":
        (bootstrap / "versioned_runtime_leases.py").mkdir()
    if layout == "corrupt":
        (bootstrap / "versioned_runtime_leases.py").write_text("def _publication_lease(:")
    if layout == "truncated":
        (bootstrap / "versioned_runtime_leases.py").write_text(
            "def _publication_lease(root, *, timeout=60):\n"
            "    return _kernel_lease(root, timeout=timeout)\n",
        )
    monkeypatch.setattr(service, "run_dir", lambda: tmp_path / "run")
    monkeypatch.setattr(generation, "home_dir", lambda: tmp_path)
    monkeypatch.setattr(generation, "PROCESS_SLOT", tmp_path if layout == "foreign-slot" else slot)
    controller = generation.ServiceGeneration(SimpleNamespace(), SimpleNamespace(), [], [])
    controller.predecessor = SimpleNamespace(
        retire=lambda *_: pytest.fail("No selector custody permits native retirement"),
    )
    guard = generation.selection_custody(tmp_path, slot) if layout == "busy" else nullcontext()
    with guard:
        assert controller._retire_previous() == "refused-selection-custody"
    controller.publication.acquire()
    controller.publication.release()


@pytest.mark.skipif(os.name != "nt" and sys.platform != "linux", reason="Native pinned retirement")
def test_native_repeated_replacement_excludes_the_actual_older_writer(
    tmp_path: Path,
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from zdd.private_protocol import Deadline

    for identity in ("newer", "middle", "older"):
        _stage_generation(tmp_path, identity)
    (tmp_path / "current-version").write_text("newer")
    durable = tmp_path / "fixture.kdbx"
    durable.write_bytes(b"fixture durable bytes")
    old = _start(tmp_path, "newer", 0, "hung-work")
    children = [old]
    lifetime = None
    try:
        _until(lambda: _endpoint(tmp_path) is not None, children)
        first = _endpoint(tmp_path)
        lifetime = _capture_process(first["pid"])
        with ThreadPoolExecutor(max_workers=1) as worker:
            pending = worker.submit(
                _send, _target(first), {"action": "has", "kpdb": str(durable), "entry": "fixture"},
            )
            _until(lambda: (tmp_path / "newer.entered").exists(), children)
            (tmp_path / "current-version").write_text("middle")
            middle = _start(tmp_path, "middle", 0, "passive-legacy")
            children.append(middle)
            _until(lambda: (_endpoint(tmp_path) or {}).get("pid") not in {None, first["pid"]}, children)
            second = _endpoint(tmp_path)
            assert not _send(_target(second), {"action": "ping"})["accepting_work"]
            (tmp_path / "current-version").write_text("older")
            new = _start(tmp_path, "older", 0, "bounded-successor")
            children.append(new)
            _until(
                lambda: (_endpoint(tmp_path) or {}).get("pid") not in {None, first["pid"], second["pid"]},
                [new],
            )
            incoming = _endpoint(tmp_path)
            port = _target(incoming)
            _until(lambda: _send(port, {"action": "ping"})["accepting_work"], [new], timeout=8)
            lifetime.wait_exit(Deadline(1))
            old.communicate(timeout=2)
            middle.communicate(timeout=2)
            assert old.returncode != 0 and middle.returncode == 0
            with pytest.raises((ValueError, OSError)):
                pending.result(timeout=2)
            (tmp_path / "older.release").touch()
            assert _send(port, {"action": "has", "kpdb": str(durable), "entry": "fixture"})["ok"]
            assert durable.read_bytes() == b"fixture durable bytes"
            assert _endpoint(tmp_path)["pid"] == incoming["pid"]
            if os.name != "nt":
                _until(lambda: _serving(str(tmp_path / "vault.sock"), incoming["pid"]), [new])
    finally:
        _settle(tmp_path, children)
        if lifetime is not None:
            lifetime.close()
    assert not (tmp_path / "vault.pid").exists()


@pytest.mark.skipif(sys.platform != "linux", reason="Native Linux fixed Unix alias")
def test_successive_cold_replacements_recover_the_fixed_unix_alias(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    identities = ("newer", "middle", "older")
    for identity in identities:
        _stage_generation(tmp_path, identity)
    (tmp_path / "current-version").write_text("newer")
    durable = tmp_path / "fixture.kdbx"
    durable.write_bytes(b"fixture durable bytes")
    children = [_start(tmp_path, "newer", 0, "bounded-successor")]
    fixed = str(tmp_path / "vault.sock")
    try:
        _until(lambda: _endpoint(tmp_path) is not None, children)
        endpoint = _endpoint(tmp_path)
        for outgoing, incoming in zip(identities, identities[1:]):
            _until(lambda: _serving(fixed, endpoint["pid"]), [children[-1]])
            with ThreadPoolExecutor(max_workers=1) as worker:
                pending = worker.submit(
                    _send, fixed, {"action": "has", "kpdb": str(durable), "entry": "fixture"},
                )
                _until(lambda: (tmp_path / (outgoing + ".entered")).exists(), [children[-1]])
                (tmp_path / "current-version").write_text(incoming)
                previous = children[-1]
                new = _start(tmp_path, incoming, 0, "bounded-successor")
                children.append(new)
                old_pid = endpoint["pid"]
                _until(lambda: (_endpoint(tmp_path) or {}).get("pid") not in {None, old_pid}, [new])
                endpoint = _endpoint(tmp_path)
                assert endpoint["endpoint"] != fixed
                _until(lambda: _send(_target(endpoint), {"action": "ping"})["accepting_work"], [new])
                previous.communicate(timeout=2)
                assert previous.returncode != 0
                with pytest.raises((OSError, ValueError)):
                    pending.result(timeout=2)
            _until(lambda: _serving(fixed, endpoint["pid"]), [new])
        assert _send(fixed, {"action": "ping"})["pid"] == children[-1].pid
        assert durable.read_bytes() == b"fixture durable bytes"
    finally:
        _settle(tmp_path, children)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows legacy TCP restoration")
def test_windows_alias_retries_after_discovery_exit_while_older_transport_survives(
    tmp_path: Path,
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from agent_vault.winpipe import pipe_send

    for identity in ("newer", "middle", "older"):
        _stage_generation(tmp_path, identity)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        fixed_port = reservation.getsockname()[1]
    (tmp_path / "current-version").write_text("newer")
    durable = tmp_path / "fixture.kdbx"
    durable.write_bytes(b"fixture durable bytes")
    old = _start(tmp_path, "newer", fixed_port, "hung-cleanup")
    children = [old]
    try:
        _until(lambda: _endpoint(tmp_path) is not None, children)
        first = _endpoint(tmp_path)
        assert first["transport"] == "pipe"
        with ThreadPoolExecutor(max_workers=1) as worker:
            pending = worker.submit(
                _send, fixed_port, {"action": "has", "kpdb": str(durable), "entry": "fixture"},
            )
            _until(lambda: (tmp_path / "newer.entered").exists(), children)
            (tmp_path / "current-version").write_text("middle")
            middle = _start(tmp_path, "middle", fixed_port, "passive-legacy")
            children.append(middle)
            _until(lambda: (_endpoint(tmp_path) or {}).get("pid") not in {None, first["pid"]}, children)
            second = _endpoint(tmp_path)
            (tmp_path / "newer.release").touch()
            assert pending.result(timeout=10)["ok"]
            _until(lambda: (tmp_path / "cleanup.entered").exists(), children)
            _until(lambda: _send(_target(second), {"action": "ping"})["accepting_work"], children)
        (tmp_path / "current-version").write_text("older")
        new = _start(tmp_path, "older", fixed_port, "bounded-successor")
        children.append(new)
        _until(
            lambda: (_endpoint(tmp_path) or {}).get("pid") not in {None, first["pid"], second["pid"]},
            [new],
        )
        incoming = _endpoint(tmp_path)
        _until(lambda: _send(_target(incoming), {"action": "ping"})["accepting_work"], [new])
        middle.communicate(timeout=3)
        assert middle.returncode == 0 and old.poll() is None
        with socket.socket() as occupied:
            with pytest.raises(OSError):
                occupied.bind(("127.0.0.1", fixed_port))
        time.sleep(1.1)  # Allow two real lifecycle ticks after discovery-owner exit.
        (tmp_path / "newer.stop").touch()
        old.communicate(timeout=3)
        assert old.returncode == 0
        _until(lambda: _serving(fixed_port, incoming["pid"]), [new], timeout=4)
        _until(
            lambda: (pipe_send(first["endpoint"], {"action": "ping"}, timeout=.25) or {}).get("pid")
            == incoming["pid"],
            [new], timeout=4,
        )
        assert durable.read_bytes() == b"fixture durable bytes"
    finally:
        _settle(tmp_path, children)


def test_failed_successor_does_not_remove_or_stop_the_incumbent(tmp_path: Path) -> None:
    (tmp_path / "current-version").write_text("newer")
    old = _start(tmp_path, "newer", 0)
    children = [old]
    try:
        _until(lambda: _endpoint(tmp_path) is not None, children)
        endpoint = _endpoint(tmp_path)
        old_port = _target(endpoint)
        (tmp_path / "current-version").write_text("older")
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


@pytest.mark.skipif(os.name != "nt" and sys.platform != "linux", reason="Native pinned retirement")
@pytest.mark.parametrize("old_mode", ("hung-cleanup", "hung-work", "backend-work"))
def test_native_hung_predecessor_preserves_grace_then_excludes_only_its_writer(
    tmp_path: Path, old_mode: str,
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from zdd.private_protocol import Deadline

    for identity in ("newer", "older"):
        _stage_generation(tmp_path, identity)
    (tmp_path / "current-version").write_text("newer")
    durable = tmp_path / "fixture.kdbx"
    durable.write_bytes(b"fixture durable bytes")
    old = _start(tmp_path, "newer", 0, old_mode)
    children = [old]
    lifetime = None
    try:
        _until(lambda: _endpoint(tmp_path) is not None, children)
        initial = _endpoint(tmp_path)
        old_pid, old_port = initial["pid"], _target(initial)
        lifetime = _capture_process(old_pid)
        if old_mode == "backend-work":
            new = _start(tmp_path, "older", 0, "prepared-successor")
            children.append(new)
            _until(lambda: (tmp_path / "older.prepared").exists(), children)
        with ThreadPoolExecutor(max_workers=1) as worker:
            pending = worker.submit(
                _send, old_port, {"action": "has", "kpdb": str(durable), "entry": "fixture"},
            )
            _until(lambda: (tmp_path / "newer.entered").exists(), children)
            if old_mode == "backend-work":
                _until(lambda: (tmp_path / "backend.entered").exists(), children)
            (tmp_path / "current-version").write_text("older")
            if old_mode == "backend-work":
                (tmp_path / "older.start").touch()
            else:
                new = _start(tmp_path, "older", 0, "bounded-successor")
                children.append(new)
            _until(lambda: (_endpoint(tmp_path) or {}).get("pid") not in {None, old_pid}, children)
            incoming = _endpoint(tmp_path)
            port, pid = _target(incoming), incoming["pid"]
            assert old.poll() is None
            assert not _send(port, {"action": "ping"})["accepting_work"]
            assert _send(port, {"action": "has", "kpdb": str(durable), "entry": "fixture"}) == {
                "ok": False, "error": "service_transition", "retryable": True,
            }
            assert _send(old_port, {"action": "ping"})["pid"] == old_pid
            if old_mode == "hung-cleanup":
                (tmp_path / "newer.release").touch()
                assert pending.result(timeout=10)["ok"]
                _until(lambda: (tmp_path / "cleanup.entered").exists(), children)
                _until(lambda: _send(port, {"action": "ping"})["accepting_work"], children)
                assert old.poll() is None
                assert _send(port, {"action": "ping"})["auth_handoff"] == "warmed"
            _until(
                lambda: _send(port, {"action": "ping"})["predecessor_retirement"] != "grace",
                [new],
            )
            if old_mode == "backend-work":
                assert _send(port, {"action": "ping"})["predecessor_retirement"] == "refused-live-workers"
                assert old.poll() is None
                assert not _send(port, {"action": "ping"})["accepting_work"]
                assert not (tmp_path / "backend.finished").exists()
                assert not (tmp_path / "older.entered").exists()
                assert not (tmp_path / "backend.expired").exists(), "Eight-second backend fixture expired"
                (tmp_path / "backend.release").touch()
                assert pending.result(timeout=5)["ok"]
                assert (tmp_path / "backend.finished").exists()
            else:
                assert _send(port, {"action": "ping"})["predecessor_retirement"] == "cold-retired"
            lifetime.wait_exit(Deadline(5))
            old.communicate(timeout=2)
            assert (old.returncode == 0) is (old_mode == "backend-work")
            if old_mode == "hung-work":
                with pytest.raises((ValueError, OSError)):
                    pending.result(timeout=2)
                assert not (tmp_path / "newer.release").exists()
            _until(lambda: _send(port, {"action": "ping"})["accepting_work"], [new])
            if old_mode == "backend-work":
                _until(
                    lambda: _send(port, {"action": "ping"})["predecessor_retirement"] == "exited",
                    [new],
                )
            health = _send(port, {"action": "ping"})
            assert health["predecessor_retirement"] in {"cold-retired", "exited"}
            assert health["unlocked_vaults"] == ([str(durable)] if old_mode != "hung-work" else [])
            assert _endpoint(tmp_path)["pid"] == pid
            if os.name != "nt":
                from agent_vault import service

                def unix_alias_ready() -> bool:
                    response = service._dial_unix(
                        {"action": "ping", "health_only": True},
                        str(tmp_path / "vault.sock"), timeout=.25,
                    )
                    return bool(response and response.get("pid") == pid)

                _until(unix_alias_ready, [new])
            (tmp_path / "older.release").touch()
            assert _send(port, {"action": "has", "kpdb": str(durable), "entry": "fixture"})["ok"]
            work = [json.loads(row) for row in (tmp_path / "work.jsonl").read_text().splitlines()]
            assert work == [
                ["newer", "entered"],
                *([["newer", "settled"]] if old_mode != "hung-work" else []),
                ["older", "entered"], ["older", "settled"],
            ]
            assert durable.read_bytes() == b"fixture durable bytes"
    finally:
        outputs = _settle(tmp_path, children)
        if lifetime is not None:
            lifetime.close()
    assert children[-1].returncode == 0
    assert all("fixture-master" not in output for output in outputs)
    assert _endpoint(tmp_path) is None
