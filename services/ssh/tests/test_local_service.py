"""Exact cohort reuse and independent local lifecycle outcomes."""
import sys
import asyncio
import threading
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from agent_ssh_service import local_control, local_service
from agent_ssh_service.local_control import config_identity
from agent_ssh_service.config import Document, ServiceConfig, decode


@pytest.fixture(autouse=True)
def source_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_ssh_service import source_start

    monkeypatch.setattr(source_start, "ensure_selected", lambda _: None)
    monkeypatch.setattr(source_start, "load_provider", lambda _: SimpleNamespace(
        _resources=lambda _: (None, SimpleNamespace(_native_retained_path=str)),
    ))


def binding(root: Path) -> SimpleNamespace:
    root.mkdir()
    slot = root / "versions" / "fixture-generation"
    slot.mkdir(parents=True)
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir()
    (bootstrap / "runtime_launcher.py").write_text("# actor-free fixture", encoding="utf-8")
    return SimpleNamespace(
        root=root, slot=slot, python=slot / ".venv" / "Scripts" / "python.exe",
        identity=SimpleNamespace(document=lambda: {"fixture": "service-source"}),
    )


def owner_record(selected: SimpleNamespace, config: ServiceConfig) -> Document:
    return {
        "root": str(selected.root), "slot": selected.slot.name,
        "source": selected.identity.document(), "config_identity": config_identity(selected, config),
        "peer": {"pid": 123, "start_time": "456", "principal": "fixture"},
    }


def test_exact_healthy_owner_reused_without_spawn_or_stop(
    config: ServiceConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = binding(tmp_path / "runtime")
    record = owner_record(selected, config)
    calls = []
    monkeypatch.setattr(local_service, "records", lambda _: [record])
    monkeypatch.setattr(local_service, "spawn_daemon", lambda *a, **k: pytest.fail("spawned"))
    def health(_config: ServiceConfig, _record: Document, action: str, **_: Any) -> Document:
        calls.append(action)
        return {"ready": True}
    monkeypatch.setattr(local_service, "request", health)
    assert local_service.start(selected, tmp_path / "policy.json", config) == {
        "state": "ready", "reused": True,
    }
    assert calls == ["health"]


def test_unready_owner_is_preserved_and_failed_candidate_stays_attributable(
    config: ServiceConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = binding(tmp_path / "runtime")
    monkeypatch.setattr(local_service, "records", lambda _: [owner_record(selected, config)])
    candidate = SimpleNamespace(pid=456, poll=lambda: 1)
    monkeypatch.setattr(local_service, "spawn_daemon", lambda *a, **k: candidate)
    monkeypatch.setattr(local_service, "request", lambda *a, **_: {"ready": False})
    with pytest.raises(local_service.LocalStartupUncertain) as caught:
        local_service.start(selected, tmp_path / "policy.json", config)
    assert caught.value.process is candidate


def test_different_source_owner_is_preserved_without_control(
    config: ServiceConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = binding(tmp_path / "runtime")
    record = owner_record(selected, config)
    record["slot"] = "other-generation"
    monkeypatch.setattr(local_service, "records", lambda _: [record])
    monkeypatch.setattr(local_service, "request", lambda *a, **k: pytest.fail("controlled predecessor"))
    candidate = SimpleNamespace(pid=456, poll=lambda: 1)
    monkeypatch.setattr(local_service, "spawn_daemon", lambda *a, **k: candidate)
    with pytest.raises(local_service.LocalStartupUncertain):
        local_service.start(selected, tmp_path / "policy.json", config)


@pytest.mark.parametrize("old_root", ["missing", "unrelated", "inaccessible"])
def test_stale_root_record_does_not_veto_current_data_owner(
    config: ServiceConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, old_root: str,
) -> None:
    selected = binding(tmp_path / "runtime")
    current = owner_record(selected, config)
    stale = dict(current, root=str(tmp_path / old_root))
    if old_root == "unrelated":
        (tmp_path / old_root).mkdir()
    if old_root == "inaccessible":
        original = Path.samefile
        def samefile(path: Path, other: Any) -> bool:
            if path == tmp_path / old_root:
                raise PermissionError("fixture root uninspectable")
            return original(path, other)
        monkeypatch.setattr(Path, "samefile", samefile)
    monkeypatch.setattr(local_service, "records", lambda _: [stale, current])
    monkeypatch.setattr(local_service, "spawn_daemon", lambda *a, **k: pytest.fail("spawned"))
    monkeypatch.setattr(local_service, "request", lambda *a, **k: {"ready": True})
    assert local_service.start(selected, tmp_path / "policy.json", config)["reused"]
    assert stale["root"] == str(tmp_path / old_root)


@pytest.mark.parametrize("uninspectable", [False, True])
def test_native_live_or_uninspectable_record_is_never_reclaimed(
    config: ServiceConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    uninspectable: bool,
) -> None:
    from fleet_manager.private_state import private_directory
    import zdd.diagnostics as diagnostics

    record = owner_record(binding(tmp_path / "runtime"), config)
    record["peer"] = asdict(local_control.current_peer())
    cell = local_control.cell_directory(config)
    private_directory(cell)
    path = cell / "owner-retained.json"
    local_control.publish_new(path, record)
    if uninspectable:
        monkeypatch.setattr(diagnostics, "_pid_alive", lambda _: True)
        monkeypatch.setattr(diagnostics, "process_start_time", lambda _: None)
    assert local_control.records(config) == [record]
    assert path.exists()


def test_changed_owner_file_cannot_be_removed_as_old_dead_identity(
    config: ServiceConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_manager.private_state import private_directory

    record = owner_record(binding(tmp_path / "runtime"), config)
    cell = local_control.cell_directory(config)
    private_directory(cell)
    path = cell / "owner-replaced.json"
    local_control.publish_new(path, record)
    replacement = dict(record, peer=asdict(local_control.current_peer()))
    def replaced(_: Document) -> bool:
        path.unlink()
        local_control.publish_new(path, replacement)
        return False
    monkeypatch.setattr(local_control, "lock_data_is_live", replaced)
    with pytest.raises(ValueError, match="record-changed"):
        local_control.records(config)
    assert decode(path.read_bytes()) == replacement


def test_33_live_records_keep_bound_and_preserve_every_owner(
    config: ServiceConfig, tmp_path: Path,
) -> None:
    from fleet_manager.private_state import private_directory

    record = owner_record(binding(tmp_path / "runtime"), config)
    record["peer"] = asdict(local_control.current_peer())
    cell = local_control.cell_directory(config)
    private_directory(cell)
    for index in range(33):
        local_control.publish_new(cell / f"owner-live-{index}.json", record)
    with pytest.raises(ValueError, match="inventory-limit"):
        local_control.records(config)
    assert len(list(cell.glob("owner-*.json"))) == 33


@pytest.mark.asyncio
async def test_control_shutdown_drains_owned_disk_work_without_blocking_loop(
    config: ServiceConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_ssh_service.gateway import SSHService

    control = local_control.LocalControl(binding(tmp_path / "runtime"), config, selected=lambda _: None)
    store = control.writer.activate()
    entered, release = threading.Event(), threading.Event()
    original = store.snapshot
    def snapshot() -> Document:
        with store._transaction():
            entered.set()
            assert release.wait(5), "fixture disk worker release missing"
        return original()
    monkeypatch.setattr(store, "snapshot", snapshot)
    ready = asyncio.Event()
    ready.set()
    service = SSHService(store)
    control.initialized = asyncio.Event()
    control.stop = asyncio.Event()
    control.attach(
        store=store, service=service, connector=cast(Any, SimpleNamespace(connected=ready)),
        relay=None, stop=control.stop, lease=cast(Any, None),
    )
    health = asyncio.create_task(control._operate("health"))
    closing = None
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        closing = asyncio.create_task(control.close())
        await asyncio.sleep(.05)
        assert not closing.done(), "control released ownership before disk work settled"
        assert control.operations
    finally:
        release.set()
        if closing is not None:
            await closing
        else:
            health.cancel()
            await control.close()
        with pytest.raises(asyncio.CancelledError):
            await health
        await service.close()
    assert not control.operations


@pytest.mark.parametrize("phase", ["claim", "precommit"])
@pytest.mark.parametrize("withdrawal", ["stop", "closed"])
def test_withdrawn_control_cannot_claim_or_commit_old_owner_fence(
    config: ServiceConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    phase: str, withdrawal: str,
) -> None:
    from agent_ssh_service.writer import FencedWriter
    from agent_ssh_service.store import DirectoryStore

    outgoing = FencedWriter(config, "outgoing")
    store = outgoing.activate()
    before = store.snapshot()
    incoming = local_control.LocalControl(binding(tmp_path / "runtime"), config, selected=lambda _: None)
    incoming.stop = asyncio.Event()
    flag = incoming.stop if withdrawal == "stop" else incoming.closed
    if phase == "claim":
        flag.set()
    else:
        original = DirectoryStore._accept_configuration
        def accepted(candidate: DirectoryStore, db: Any) -> None:
            original(candidate, db)
            flag.set()
        monkeypatch.setattr(DirectoryStore, "_accept_configuration", accepted)
    with pytest.raises(ValueError, match="stopping"):
        incoming.writer.activate()
    assert outgoing.current() and store.snapshot() == before


def test_explicit_stop_waits_for_exact_native_exit(
    config: ServiceConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = binding(tmp_path / "runtime")
    record = owner_record(selected, config)
    monkeypatch.setattr(local_service, "records", lambda _: [record])
    calls = []
    monkeypatch.setattr(
        local_service, "request",
        lambda actual, target, action, **options: calls.append((actual, target, action, options)),
    )
    local_service.stop(selected, config)
    assert calls == [(config, record, "stop", {"wait_exit": True})]


@pytest.mark.parametrize("data_alias", [False, True])
def test_new_owner_launches_native_source_qualified_interpreter_not_alternate(
    config: ServiceConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    data_alias: bool,
) -> None:
    if data_alias:
        (tmp_path / "existing").mkdir()
        document = config.to_dict()
        document["data_dir"] = str(tmp_path / "existing" / ".." / "state")
        config = ServiceConfig.from_dict(document)
    original_config = config.encoded
    selected = binding(tmp_path / "runtime")
    candidate = SimpleNamespace(pid=456, poll=lambda: None)
    launched: list[list[str]] = []
    owner = owner_record(selected, config)
    owner["peer"]["pid"] = candidate.pid
    monkeypatch.setattr(sys, "executable", str(tmp_path / "unadmitted-python.exe"))
    from agent_ssh_service import source_start

    conversions = []
    native = "\\\\?\\" + str(selected.python)

    def convert(path: Path) -> str:
        assert path == selected.python
        conversions.append(path)
        return native

    monkeypatch.setattr(source_start, "load_provider", lambda slot: SimpleNamespace(
        _resources=lambda actual: (
            None, SimpleNamespace(_native_retained_path=convert),
        ) if actual == slot == selected.slot else pytest.fail("foreign slot"),
    ))
    monkeypatch.setattr(local_service, "records", lambda _: [owner] if launched else [])

    def spawn(command: list[str], **options: Any) -> SimpleNamespace:
        assert command[0] == native, "Source-qualified native spelling was not forwarded"
        physical_data = Path(config.to_dict()["data_dir"]).resolve()
        expected_cwd = Path(physical_data.anchor) if os.name == "nt" and len(str(physical_data)) >= 260 else physical_data
        assert options["cwd"] == expected_cwd
        assert options["instance"].runtime_root == physical_data
        assert config.encoded == original_config
        launched.append(command)
        return candidate

    monkeypatch.setattr(local_service, "spawn_daemon", spawn)
    monkeypatch.setattr(
        local_service, "request",
        lambda _config, _record, operation, **_: (
            {"prepared": True} if operation == "prepare" else {"ready": True}
        ),
    )
    assert local_service.start(selected, tmp_path / "policy.json", config) == {
        "state": "ready", "reused": False,
    }
    assert conversions == [selected.python]


@pytest.mark.parametrize("extra_entry", [False, True])
def test_posix_projected_request_uses_short_owned_socket_directory(
    config: ServiceConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra_entry: bool,
) -> None:
    """Transport projection only: no Linux peer-pidfd claim from this host test."""
    import zdd.private_unix as unix
    from zdd.private_protocol import PeerIdentity
    selected = binding(tmp_path / "runtime")
    record = owner_record(selected, config)
    caller = PeerIdentity(111, "222", "fixture")
    real_os = os
    monkeypatch.setattr(local_control, "os", SimpleNamespace(**{**vars(real_os), "name": "posix"}))
    monkeypatch.setattr(local_control, "current_peer", lambda: caller)
    temporary: list[Path] = []
    repo = Path(__file__).resolve().parents[3]
    def create(*, prefix: str) -> str:
        path = Path(tempfile.mkdtemp(prefix=prefix, dir=repo / ".test-venvs"))
        temporary.append(path)
        return str(path)
    monkeypatch.setattr(local_control, "mkdtemp", create, raising=False)
    from fleet_manager.private_state import private_directory as real_private
    validated = []
    def private(path: Path) -> None:
        if path in temporary:
            validated.append(path)  # POSIX privacy is a source contract, not NT ACL emulation.
        else:
            real_private(path)
    monkeypatch.setattr(local_control, "private_directory", private)
    class Listener:
        def __init__(self, path: Path) -> None:
            assert len(os.fsencode(str(path))) <= 107, "long data root leaked into Unix socket address"
            assert path.parent in temporary
            self.path = path
        def accept(self, deadline: Any, *, expected: PeerIdentity) -> Any:
            assert expected == PeerIdentity(**record["peer"])
            return SimpleNamespace(
                retain_peer=lambda _: SimpleNamespace(close=lambda: None), close=lambda: None)
        def close(self) -> None:
            if extra_entry:
                (self.path.parent / "unexpected").write_text("preserved")
    monkeypatch.setattr(unix, "UnixListener", Listener)
    monkeypatch.setattr(local_control, "write_frame", lambda *args: None)
    monkeypatch.setattr(local_control, "read_frame", lambda *args: {
        "pid": 123, "process_start_time": "456", "version": record["slot"],
        "config_identity": record["config_identity"], "ready": True,
    })
    try:
        if extra_entry:
            with pytest.raises(OSError):
                local_control.request(config, record, "health")
            assert (temporary[0] / "unexpected").read_text() == "preserved"
        else:
            assert local_control.request(config, record, "health")["ready"]
            assert not temporary[0].exists()
        assert validated == temporary
    finally:
        for path in temporary:
            if (path / "unexpected").exists():
                (path / "unexpected").unlink()
            if path.exists():
                path.rmdir()
