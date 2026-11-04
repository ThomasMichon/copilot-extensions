"""Private process/native-channel contract, with synthetic connector readiness."""
from __future__ import annotations

from dataclasses import asdict
import json
import secrets
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
if mode == 'initializing':
    original_activate = FencedWriter.activate
    def failed_initialization(self):
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
raise SystemExit(asyncio.run(local_service.serve(binding, config)))
"""


def recovery_child(
    config: ServiceConfig, tmp_path: Path, environment: dict[str, str], mode: str,
) -> tuple[Any, Path]:
    slot = tmp_path / "runtime" / "versions" / ("fixture-" + secrets.token_hex(4))
    slot.mkdir(parents=True)
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps(config.to_dict()))
    log = tmp_path / (slot.name + ".log")
    process = spawn_daemon(
        [sys.executable, "-I", "-B", "-c", _RECOVERY_CHILD, str(policy), str(slot), mode,
         json.dumps(asdict(local_control.current_peer())), str(tmp_path / "handled")],
        cwd=Path(config.to_dict()["data_dir"]), log_path=log, env=environment,
        instance=DaemonInstanceSpec(
            "agent-ssh-service", Path(config.to_dict()["data_dir"]), Path(entry.__file__)),
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
