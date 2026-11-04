"""Private process/native-channel contract, with synthetic connector readiness."""
from __future__ import annotations

from dataclasses import asdict
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import secrets
import shutil
import stat
import tempfile
import threading
import os
from pathlib import Path
import sqlite3
import sys
import time
from collections.abc import Iterator
from typing import Any, TYPE_CHECKING, cast

import pytest

from agent_procutil import spawn_daemon
from agent_procutil.daemon_instances import DaemonInstanceSpec, daemon_test_lifecycle
from agent_ssh_service import __main__ as entry
from agent_ssh_service import local_control
from agent_ssh_service.local_control import records, request
from agent_ssh_service.store import DirectoryStore
from agent_ssh_service.config import Document, ServiceConfig
from single_instance_lease import SingleInstance
from fleet_manager.private_state import create_private_file
if TYPE_CHECKING:
    from services.ssh.tests.conftest import enrollment
    from zdd.private_protocol import Channel, Deadline
else:
    from conftest import enrollment


@pytest.fixture
def native_environment(tmp_path: Path) -> Iterator[dict[str, str]]:
    cleanup: list[Any] = []
    with daemon_test_lifecycle(
        owner="ssh-local-native-contract", registry_root=tmp_path / "registry",
        deadline=time.monotonic() + 60, cleanup_results=cleanup,
    ) as environment:
        yield {**os.environ, **environment}
    assert cleanup and all(result.status == "settled" for result in cleanup)
    print(json.dumps({"native_cleanup": [asdict(result) for result in cleanup]}))


@pytest.mark.parametrize("transfer", [False, True])
def test_private_process_attests_actual_foreground_and_exits_on_owned_stop(
    config: ServiceConfig, tmp_path: Path, native_environment: dict[str, str],
    transfer: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    slot = root / "versions" / "fixture-generation"
    slot.mkdir(parents=True)
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps(config.to_dict()), encoding="utf-8")
    seed_lease = SingleInstance(config.to_dict()["data_dir"], service="agent-ssh-service")
    os.close(create_private_file(seed_lease.lock_path))
    seed_lease.acquire()
    try:
        DirectoryStore(config).enroll("principal-worker-a", enrollment())
    finally:
        seed_lease.release()
    script = r"""
import asyncio, json, pathlib, sys
from types import SimpleNamespace
from agent_ssh_service import hosting, local_service, source_start
from agent_ssh_service.config import ServiceConfig
config = ServiceConfig.load(pathlib.Path(sys.argv[1]))
slot = pathlib.Path(sys.argv[2])
binding = SimpleNamespace(
    root=slot.parent.parent, slot=slot,
    identity=SimpleNamespace(document=lambda: {'fixture':'service-source'}),
)
async def no_access():
    raise AssertionError('no credential may be acquired')
hosting.load_service_credentials = lambda **_: SimpleNamespace(
    access_token=no_access, proof_key=None,
)
async def local_connected(connector, stop):
    connector.connected.set()
    try:
        await stop.wait()
    finally:
        connector.connected.clear()
hosting.ServiceConnector.run = local_connected
source_start.ensure_selected = lambda binding: None
# This synthetic Source has no canonical selection; it tests explicit custody,
# not automatic supersession retirement. Real generated Sources own that proof.
source_start.selection_reader = lambda binding: lambda: None
raise SystemExit(asyncio.run(local_service.serve(binding, config)))
"""
    diagnostic = tmp_path / "child.log"
    process = spawn_daemon(
        [sys.executable, "-I", "-B", "-c", script, str(policy), str(slot)],
        cwd=Path(config.to_dict()["data_dir"]), log_path=diagnostic,
        env=native_environment,
        instance=DaemonInstanceSpec(
            "agent-ssh-service", Path(config.to_dict()["data_dir"]), Path(entry.__file__),
        ),
    )
    successor = None
    extra: list[tuple[Any, Document | None]] = []
    with diagnostic.open("rb") as output:
        try:
            deadline = time.monotonic() + 15
            owner = None
            while time.monotonic() < deadline:
                available = records(config)
                if available:
                    owner = available[0]
                    break
                if process.poll() is not None:
                    break
                time.sleep(0.05)
            output.seek(0)
            assert owner is not None, output.read().decode("utf-8", errors="replace")
            assert owner["peer"]["pid"] == process.pid
            prepared = request(config, owner, "prepare")
            assert prepared["prepared"] is True
            assert request(config, owner, "health")["ready"] is False
            assert request(config, owner, "activate")["ready"] is True
            first = request(config, owner, "health")
            assert first["ready"] is True
            contender = SingleInstance(config.to_dict()["data_dir"], service="agent-ssh-service")
            contender.acquire()
            contender.release()
            assert request(config, owner, "health") == first
            if transfer:
                next_slot = slot.with_name("successor-generation")
                next_slot.mkdir()
                successor = spawn_daemon(
                    [sys.executable, "-I", "-B", "-c", script, str(policy), str(next_slot)],
                    cwd=Path(config.to_dict()["data_dir"]),
                    log_path=tmp_path / "successor.log", env=native_environment,
                    instance=DaemonInstanceSpec(
                        "agent-ssh-service", Path(config.to_dict()["data_dir"]),
                        Path(entry.__file__),
                    ),
                )
                deadline = time.monotonic() + 10
                incoming = None
                while time.monotonic() < deadline and successor.poll() is None:
                    incoming = next(
                        (row for row in records(config) if row["peer"]["pid"] == successor.pid),
                        None,
                    )
                    if incoming is not None:
                        assert request(config, incoming, "prepare")["prepared"] is True
                        assert request(config, incoming, "health")["ready"] is False
                        real_read = local_control.read_frame

                        def lost_reply(channel: Channel, deadline: Deadline) -> None:
                            assert real_read(channel, deadline)["ready"] is True
                            raise TimeoutError("fixture-lost-activation-reply")

                        with monkeypatch.context() as patch:
                            patch.setattr(local_control, "read_frame", lost_reply)
                            with pytest.raises(TimeoutError, match="lost-activation-reply"):
                                request(config, incoming, "activate")
                        assert request(config, incoming, "health")["ready"] is True
                        assert request(config, incoming, "activate")["ready"] is True
                        break
                    incoming = None
                    time.sleep(0.05)
                assert incoming is not None, "incoming must serve before incumbent retirement"
                assert process.poll() is None
                assert request(config, owner, "health")["ready"] is False
                previous = incoming
                for selected_slot in (slot, slot.with_name("repair-generation")):
                    selected_slot.mkdir(exist_ok=True)
                    candidate = spawn_daemon(
                        [sys.executable, "-I", "-B", "-c", script, str(policy), str(selected_slot)],
                        cwd=Path(config.to_dict()["data_dir"]),
                        log_path=tmp_path / (selected_slot.name + ".log"),
                        env=native_environment,
                        instance=DaemonInstanceSpec(
                            "agent-ssh-service", Path(config.to_dict()["data_dir"]),
                            Path(entry.__file__),
                        ),
                    )
                    extra.append((candidate, None))
                    deadline = time.monotonic() + 10
                    current = None
                    while time.monotonic() < deadline and candidate.poll() is None:
                        current = next(
                            (row for row in records(config)
                             if row["peer"]["pid"] == candidate.pid), None,
                        )
                        if current is not None:
                            break
                        time.sleep(0.05)
                    assert current is not None
                    extra[-1] = (candidate, current)
                    assert request(config, current, "prepare")["prepared"] is True
                    assert request(config, current, "health")["ready"] is False
                    assert request(config, current, "activate")["ready"] is True
                    assert process.poll() is None and successor.poll() is None
                    assert request(config, previous, "health")["ready"] is False
                    previous = current
                for candidate, record in extra:
                    request(config, cast(Document, record), "stop", wait_exit=True)
                    assert candidate.wait(timeout=5) == 0
                request(config, incoming, "stop", wait_exit=True)
                assert successor.wait(timeout=5) == 0
            request(config, owner, "stop", wait_exit=True)
            assert process.wait(timeout=5) == 0
            assert records(config) == []
            with sqlite3.connect(Path(config.to_dict()["data_dir"]) / "directory.sqlite3") as db:
                assert db.execute("SELECT COUNT(*) FROM enrollments").fetchone()[0] == 1
                assert db.execute("SELECT revision FROM metadata").fetchone()[0] == 2
            print(json.dumps({"native_peer": owner["peer"], "exit_code": 0,
                              "discovery_removed": True, "transaction_guard_retained": True,
                              "transfer_reverse_repair": transfer,
                              "extra_peers": [cast(Document, record)["peer"]
                                              for _, record in extra]}))
        finally:
            for candidate, _ in extra:
                if candidate.poll() is None:
                    candidate.terminate()
                candidate.wait(timeout=5)
            if successor is not None:
                if successor.poll() is None:
                    successor.terminate()
                successor.wait(timeout=5)
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)


_RECOVERY_CHILD = r"""
import asyncio, json, os, pathlib, sys
from types import SimpleNamespace
from agent_ssh_service import hosting, local_service, source_start, local_control
from agent_ssh_service.config import ServiceConfig
from agent_ssh_service.writer import FencedWriter
config = ServiceConfig.load(pathlib.Path(sys.argv[1]))
slot, mode = pathlib.Path(sys.argv[2]), sys.argv[3]
binding = SimpleNamespace(root=slot.parent.parent, slot=slot,
    identity=SimpleNamespace(document=lambda: {'fixture':'service-source'}))
async def no_access():
    raise AssertionError('no credential may be acquired')
hosting.load_service_credentials = lambda **_: SimpleNamespace(access_token=no_access, proof_key=None)
if mode != 'selection-aware':
    source_start.ensure_selected = lambda _: None
    source_start.selection_reader = lambda _: lambda: None
original_prepare = local_control.LocalControl.prepare
async def prepare(self, **kwargs):
    await original_prepare(self, **kwargs)
    if mode == 'crash':
        for index in range(32):
            local_control.publish_new(self.cell / ('owner-crashed-' + str(index) + '.json'), self.record_data)
        os._exit(23)
    if mode == 'stale-requests':
        other = json.loads(sys.argv[4])
        for index in range(33):
            local_control.publish_new(self.cell / ('request-stale-' + str(index) + '.json'), {
                'peer': other, 'target': other, 'operation': 'health',
                'address': 'unused', 'config_identity': self.identity})
        async def observed():
            while len(self.handled) < 33:
                await asyncio.sleep(.01)
            pathlib.Path(sys.argv[5]).write_text('handled')
        asyncio.create_task(observed())
local_control.LocalControl.prepare = prepare
if mode in ('initializing', 'activation-cancel'):
    original_activate = FencedWriter.activate
    def failed_initialization(self):
        if mode == 'activation-cancel':
            pathlib.Path(sys.argv[5]).with_name('activation-worker-entered').write_text('entered')
            return original_activate(self)
        original_activate(self)
        raise RuntimeError('fixture-initialization-interrupted')
    FencedWriter.activate = failed_initialization
async def connected(connector, stop):
    if mode == 'connecting':
        await asyncio.sleep(.1)
        stop.set()
        return
    connector.connected.set()
    try:
        await stop.wait()
    finally:
        connector.connected.clear()
hosting.ServiceConnector.run = connected
async def foreground():
    task = asyncio.create_task(local_service.serve(binding, config))
    if mode == 'activation-cancel':
        marker = pathlib.Path(sys.argv[5])
        while not marker.with_name('activation-worker-entered').exists():
            await asyncio.sleep(.01)
        marker.with_name('activation-cancel-requested').write_text('cancel')
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    else:
        await task
raise SystemExit(asyncio.run(foreground()))
"""


def recovery_child(
    config: ServiceConfig, tmp_path: Path, environment: dict[str, str], mode: str,
) -> tuple[Any, Path]:
    slot = tmp_path / "runtime" / "versions" / ("fixture-" + secrets.token_hex(4))
    slot.mkdir(parents=True)
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps(config.to_dict()))
    log = tmp_path / (slot.name + ".log")
    from agent_ssh_service import local_service
    data = Path(config.to_dict()["data_dir"]).resolve()
    initial_cwd = getattr(local_service, "_launch_cwd", lambda path: path)(data)
    process = spawn_daemon(
        [sys.executable, "-I", "-B", "-c", _RECOVERY_CHILD, str(policy), str(slot), mode,
         json.dumps(asdict(local_control.current_peer())), str(tmp_path / "handled")],
        cwd=initial_cwd, log_path=log, env=environment,
        instance=DaemonInstanceSpec(
            "agent-ssh-service", Path(config.to_dict()["data_dir"]).resolve(), Path(entry.__file__)),
    )
    return process, log


def wait_record(config: ServiceConfig, process: Any) -> Document:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and process.poll() is None:
        found = [record for record in records(config) if record["peer"]["pid"] == process.pid]
        if found:
            return found[0]
        time.sleep(.02)
    pytest.fail("native owner registration missing")


@pytest.mark.skipif(sys.platform != "linux", reason="Actual Linux native handoff and original pidfds")
def test_linux_native_successor_self_retires_old_writer(
    config: ServiceConfig, tmp_path: Path, native_environment: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_manager.private_state import private_directory
    from agent_ssh_service.writer import FencedWriter
    from zdd.private_protocol import Deadline

    root = tmp_path / "runtime"
    private_directory(root)
    slots = [root / "versions" / name for name in ("linux-first", "linux-successor")]
    repo = Path(__file__).resolve().parents[3]
    for slot in slots:
        (slot / "runtime").mkdir(parents=True)
        # Synthetic binding, but real canonical selection reader; no completion/Source claim.
        shutil.copyfile(repo / "libs/installer-engine/runtime_application_install.py", slot / "install.py")
        for directory in ("installer-engine", "versioned-runtime"):
            for source in (repo / "libs" / directory).glob("*.py"):
                shutil.copyfile(source, slot / "runtime" / source.name)
    policy = tmp_path / "selection-aware-policy.json"
    with os.fdopen(create_private_file(policy), "w") as stream:
        json.dump(config.to_dict(), stream)
    seed = SingleInstance(config.to_dict()["data_dir"], service="agent-ssh-service")
    os.close(create_private_file(seed.lock_path))
    seed.acquire()
    try:
        DirectoryStore(config).enroll("principal-worker-a", enrollment())
    finally:
        seed.release()

    def select(slot: Path) -> None:
        temporary = root / "next-selection"
        with os.fdopen(create_private_file(temporary), "w") as stream:
            stream.write(slot.name)
        os.replace(temporary, root / "current-version")

    def launch(slot: Path) -> Any:
        return spawn_daemon(
            [sys.executable, "-I", "-B", "-c", _RECOVERY_CHILD,
             str(policy), str(slot), "selection-aware"],
            cwd=Path(config.to_dict()["data_dir"]), log_path=tmp_path / (slot.name + ".log"),
            env=native_environment,
            instance=DaemonInstanceSpec(
                "agent-ssh-service", Path(config.to_dict()["data_dir"]), Path(entry.__file__)),
        )

    references: list[Any] = []
    parent_thread = threading.current_thread()
    original_read = local_control.read_frame
    def capture(channel: Channel, deadline: Deadline) -> Document:
        if threading.current_thread() is parent_thread:
            references.append(cast(Any, channel).retain_peer(deadline))
        return original_read(channel, deadline)
    monkeypatch.setattr(local_control, "read_frame", capture)
    processes = []
    try:
        select(slots[0])
        first = launch(slots[0])
        processes.append(first)
        outgoing = wait_record(config, first)
        assert request(config, outgoing, "prepare")["prepared"]
        assert request(config, outgoing, "activate")["ready"]
        old = references[-1]
        assert asdict(old.peer) == outgoing["peer"]
        assert old.peer.principal == local_control.current_peer().principal
        assert old.peer.principal.isdecimal() and not old.exited(Deadline(1))
        with sqlite3.connect(Path(config.to_dict()["data_dir"]) / "directory.sqlite3") as db:
            before = db.execute("SELECT * FROM metadata").fetchall()
            enrollments = db.execute("SELECT * FROM enrollments").fetchall()
            assert len(enrollments) == 1
            old_owner = db.execute("SELECT owner FROM service_writer").fetchone()[0]
        select(slots[1])
        successor = launch(slots[1])
        processes.append(successor)
        incoming = wait_record(config, successor)
        assert request(config, incoming, "prepare")["prepared"]
        with ThreadPoolExecutor(max_workers=1) as workers:
            with FencedWriter(config, "fixture-guard-holder").guard():
                activation = workers.submit(request, config, incoming, "activate")
                time.sleep(.1)
                assert not activation.done() and not old.exited(Deadline(1))
            assert activation.result(timeout=10)["ready"]
        for _ in range(2):
            assert request(config, incoming, "health")["ready"]
        fresh = references[-1]
        assert asdict(fresh.peer) == incoming["peer"]
        assert old.exited(Deadline(10)) and first.wait(timeout=5) == 0
        assert not fresh.exited(Deadline(1)) and records(config) == [incoming]
        with sqlite3.connect(Path(config.to_dict()["data_dir"]) / "directory.sqlite3") as db:
            assert db.execute("SELECT * FROM metadata").fetchall() == before
            assert db.execute("SELECT * FROM enrollments").fetchall() == enrollments
            owner = db.execute("SELECT owner FROM service_writer").fetchone()[0]
            assert owner != old_owner
            assert owner == local_control.writer_identity(incoming["peer"], incoming["config_identity"])
        print(json.dumps({"Linux_native_old": outgoing["peer"], "successor": incoming["peer"],
                          "old_self_retired_without_stop": True, "successor_registration": True,
                          "actual_guard_and_SQLite_fence": True, "transport": "Unix peer credentials/pidfd",
                          "fixture_boundary": "synthetic Source binding and connected connector; real canonical selection, auth, fence and retirement"}))
    finally:
        for record in records(config):
            request(config, record, "stop", wait_exit=True)
        for process in processes:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
        for reference in references:
            reference.close()


def test_abrupt_native_exit_reconciles_33_records_before_restart(
    config: ServiceConfig, tmp_path: Path, native_environment: dict[str, str],
) -> None:
    crashed, _ = recovery_child(config, tmp_path, native_environment, "crash")
    assert crashed.wait(timeout=10) == 23
    assert len(list(local_control.cell_directory(config).glob("owner-*.json"))) == 33
    assert records(config) == []
    restarted, log = recovery_child(config, tmp_path, native_environment, "serve")
    try:
        record = wait_record(config, restarted)
        assert request(config, record, "prepare")["prepared"]
        assert request(config, record, "activate")["ready"]
        request(config, record, "stop", wait_exit=True)
        assert restarted.wait(timeout=5) == 0, log.read_text()
        assert records(config) == []
    finally:
        if restarted.poll() is None:
            restarted.terminate()
        restarted.wait(timeout=5)


def test_33_handled_stale_requests_do_not_disable_native_control(
    config: ServiceConfig, tmp_path: Path, native_environment: dict[str, str],
) -> None:
    process, log = recovery_child(config, tmp_path, native_environment, "stale-requests")
    try:
        record = wait_record(config, process)
        deadline = time.monotonic() + 3
        while not (tmp_path / "handled").exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert (tmp_path / "handled").exists(), "old request files blocked all new work"
        assert request(config, record, "prepare")["prepared"]
        assert request(config, record, "activate")["ready"]
        assert request(config, record, "health")["ready"]
        assert len(list(local_control.cell_directory(config).glob("request-stale-*.json"))) == 33
        request(config, record, "stop", wait_exit=True)
        assert process.wait(timeout=5) == 0, log.read_text()
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)


@pytest.mark.parametrize("stage", ["initializing", "connecting"])
def test_shutdown_during_native_activation_settles_control_work(
    config: ServiceConfig, tmp_path: Path, native_environment: dict[str, str], stage: str,
) -> None:
    process, log = recovery_child(config, tmp_path, native_environment, stage)
    try:
        record = wait_record(config, process)
        assert request(config, record, "prepare")["prepared"]
        with pytest.raises(Exception):
            request(config, record, "activate")
        status = process.wait(timeout=4)
        text = log.read_text()
        assert "local-control-cleanup-unconfirmed" not in text
        if stage == "initializing":
            assert "fixture-initialization-interrupted" in text
        assert status == (1 if stage == "initializing" else 0), text
        assert list(local_control.cell_directory(config).glob("owner-*.json")) == []
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=6)


def test_cancelled_native_activation_drains_guard_waiter_without_fencing_old_owner(
    config: ServiceConfig, tmp_path: Path, native_environment: dict[str, str],
) -> None:
    from agent_ssh_service.writer import FencedWriter

    outgoing = FencedWriter(config, "retained-outgoing")
    store = outgoing.activate()
    before = store.snapshot()
    process = None
    with ThreadPoolExecutor(max_workers=1) as callers:
        try:
            with outgoing.guard():
                process, log = recovery_child(config, tmp_path, native_environment, "activation-cancel")
                record = wait_record(config, process)
                activation = callers.submit(request, config, record, "activate")
                deadline = time.monotonic() + 3
                while not (tmp_path / "activation-cancel-requested").exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                assert (tmp_path / "activation-cancel-requested").exists()
                time.sleep(.15)
                retained = bool(list(local_control.cell_directory(config).glob("owner-*.json")))
                assert process.poll() is None, "queued activation lost native custody"
            with pytest.raises(Exception):
                activation.result(timeout=10)
            assert process.wait(timeout=6) == 0, log.read_text()
            assert retained, "incoming registration removed before activation worker settled"
            assert outgoing.current(), "cancelled incoming fenced the healthy outgoing owner"
            assert store.snapshot() == before
            assert records(config) == []
        finally:
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=6)


def test_admitted_external_dotdot_native_startup_reuse_keeps_config_and_fence_identity(
    config: ServiceConfig, tmp_path: Path, native_environment: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace
    from agent_ssh_service import local_service, source_start
    from fleet_manager.private_state import create_private_file

    (tmp_path / "existing").mkdir()
    document = config.to_dict()
    document["data_dir"] = str(tmp_path / "existing" / ".." / "state")
    admitted = ServiceConfig.from_dict(document)
    policy = tmp_path / "admitted.json"
    with os.fdopen(create_private_file(policy), "wb") as stream:
        stream.write(json.dumps(document).encode())
    encoded, policy_bytes = admitted.encoded, policy.read_bytes()
    selected = source_start.selected_policy(policy, SimpleNamespace(root=tmp_path / "runtime"))
    assert selected.encoded == encoded
    process, log = recovery_child(selected, tmp_path, native_environment, "serve")
    try:
        owner = wait_record(selected, process)
        assert request(selected, owner, "activate")["ready"]
        binding = SimpleNamespace(
            root=Path(owner["root"]), slot=Path(owner["root"]) / "versions" / owner["slot"],
            identity=SimpleNamespace(document=lambda: {"fixture": "service-source"}),
        )
        monkeypatch.setattr(source_start, "ensure_selected", lambda _: None)
        assert local_service.start(binding, policy, selected) == {"state": "ready", "reused": True}
        assert owner["config_identity"] == local_control.config_identity(binding, selected)
        with sqlite3.connect(Path(config.to_dict()["data_dir"]) / "directory.sqlite3") as db:
            assert db.execute("SELECT owner FROM service_writer").fetchone()[0] == local_control.writer_identity(
                owner["peer"], owner["config_identity"])
        assert selected.encoded == encoded and policy.read_bytes() == policy_bytes
        assert selected.to_dict()["proof_key_file"] == document["proof_key_file"]
        assert ".." not in local_control.cell_directory(selected).parts
        request(selected, owner, "stop", wait_exit=True)
        assert process.wait(timeout=5) == 0, log.read_text()
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=6)


@pytest.mark.skipif(os.name != "nt", reason="Native Win32 current-directory length")
def test_native_long_data_root_is_not_a_CreateProcess_current_directory(
    config: ServiceConfig, tmp_path: Path, native_environment: dict[str, str],
) -> None:
    from fleet_manager.private_state import private_directory
    data = tmp_path / ("d" * (270 - len(str(tmp_path)) - 1))
    private_directory(data)
    document = config.to_dict()
    document["data_dir"] = str(data)
    config = ServiceConfig.from_dict(document)
    assert len(str(data)) == 270
    process, log = recovery_child(config, tmp_path, native_environment, "serve")
    try:
        record = wait_record(config, process)
        assert request(config, record, "prepare")["prepared"]
        assert request(config, record, "activate")["ready"]
        assert request(config, record, "health")["ready"]
        assert len(str(local_control.cell_directory(config))) > 270
        request(config, record, "stop", wait_exit=True)
        assert process.wait(timeout=6) == 0, log.read_text()
        assert records(config) == []
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=6)


@pytest.mark.skipif(sys.platform != "linux", reason="Actual Linux SO_PEERPIDFD/private Unix IPC")
@pytest.mark.asyncio
async def test_linux_short_control_socket_keeps_actual_peer_and_private_directory(
    config: ServiceConfig, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace
    created: list[Path] = []
    def temporary(*, prefix: str) -> str:
        path = Path(tempfile.mkdtemp(prefix=prefix, dir=tmp_path_factory.getbasetemp()))
        assert stat.S_IMODE(path.stat().st_mode) == 0o700
        created.append(path)
        return str(path)
    monkeypatch.setattr(local_control, "mkdtemp", temporary)
    slot = tmp_path / "runtime/versions/fixture"
    slot.mkdir(parents=True)
    binding = SimpleNamespace(root=slot.parent.parent, slot=slot,
                              identity=SimpleNamespace(document=lambda: {"fixture": "service-source"}))
    control = local_control.LocalControl(binding, config, selected=lambda _: None)
    await control.prepare(stop=asyncio.Event(), lease=cast(Any, None))
    try:
        result = await asyncio.to_thread(request, config, control.record_data, "prepare")
        assert result["prepared"]
        assert (result["pid"], result["process_start_time"]) == (control.peer.pid, control.peer.start_time)
        assert created and all(not path.exists() for path in created)
    finally:
        await control.close()
    assert control.thread is not None and not control.thread.is_alive()


@pytest.mark.skipif(sys.platform != "linux", reason="Actual Linux Unix socket custody")
def test_linux_socket_cleanup_preserves_unexpected_entry(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[Path] = []
    def temporary(*, prefix: str) -> str:
        path = Path(tempfile.mkdtemp(prefix=prefix, dir=tmp_path_factory.getbasetemp()))
        assert stat.S_IMODE(path.stat().st_mode) == 0o700
        created.append(path)
        return str(path)
    monkeypatch.setattr(local_control, "mkdtemp", temporary)
    try:
        with pytest.raises(OSError):
            with local_control._request_listener("fixture") as (_listener, address):
                socket_path = Path(address)
                assert stat.S_ISSOCK(socket_path.lstat().st_mode)
                assert stat.S_IMODE(socket_path.lstat().st_mode) == 0o600
                (socket_path.parent / "unexpected").write_bytes(b"retained")
        assert (created[0] / "unexpected").read_bytes() == b"retained"
    finally:
        for path in created:
            (path / "unexpected").unlink(missing_ok=True)
            path.rmdir()
