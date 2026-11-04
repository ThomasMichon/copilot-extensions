"""Private process/native-channel contract, with synthetic connector readiness."""
from __future__ import annotations

from dataclasses import asdict
import json
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
