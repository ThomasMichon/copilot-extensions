"""Actor-free exchange/recovery model; not native filesystem qualification."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "plugins/agent-pull-requests/src/agent_pull_requests"
BOOT = "linux:2ad542a5-ab10-4ffa-9f4f-9f34955e03b0"


@pytest.fixture
def sealing(monkeypatch: pytest.MonkeyPatch) -> Any:
    package = ModuleType("promotion_watch_sealing")
    package.__path__ = [str(SOURCE)]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    for name in ("watch_private", "watch_registry"):
        module = ModuleType(f"{package.__name__}.{name}")
        monkeypatch.setitem(sys.modules, module.__name__, module)
        if name == "watch_private":
            for operation in ("_sync_directory", "confirm_private", "require_private", "write_private"):
                setattr(module, operation, lambda *_a, **_kw: pytest.fail("unselected storage effect"))
        else:
            setattr(module, "WatchRegistry", lambda: SimpleNamespace(restore_state=lambda *_a, **_kw: None))
    lease_module = ModuleType("single_instance_lease")
    setattr(lease_module, "SingleInstance", object)
    monkeypatch.setitem(sys.modules, lease_module.__name__, lease_module)
    update = ModuleType("update_driver")
    update.__path__ = [str(ROOT / "libs/update-driver/src/update_driver")]
    monkeypatch.setitem(sys.modules, update.__name__, update)
    modules: dict[str, Any] = {}
    for name in ("watch_storage", "watch_transition", "watch_sealing"):
        spec = importlib.util.spec_from_file_location(
            f"{package.__name__}.{name}", SOURCE / f"{name}.py",
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, module.__name__, module)
        spec.loader.exec_module(module)
        modules[name] = module
    module = modules["watch_sealing"]
    root = ROOT
    paths = module.watch_state_paths(root)
    binding = {"kind": "legacy", "stateRoot": str(root)}
    original, barrier = (2, 3), (2, 4)
    document = {
        "schema": "agent-pull-requests.watch-state-transition", "version": 1,
        "phase": "prepared", "binding": binding,
        "lease": {"device": 2, "inode": 3},
    }
    state: dict[str, Any] = {
        "document": document, "paths": {paths.historical_lock: (original, False)},
        "pin": original, "writes": [], "syncs": [], "exchanges": 0, "acquisitions": [],
    }
    monkeypatch.setattr(module, "require_private", lambda _path: None)
    monkeypatch.setattr(module, "_regular_bytes", lambda _path: json.dumps(state["document"]).encode())
    monkeypatch.setattr(module, "_pin_identity", lambda _root: state["pin"])
    monkeypatch.setattr(module, "_read_subscriptions_state", lambda _path, **_kw: [])
    monkeypatch.setattr(module, "_sync_directory", lambda path: state["syncs"].append(path))
    monkeypatch.setattr(module, "confirm_private", lambda _path: None)
    monkeypatch.setattr(module, "kernel_boot_identity", lambda: BOOT)
    monkeypatch.setattr(module.os.path, "lexists", lambda path: path in state["paths"])
    monkeypatch.setattr(Path, "is_dir", lambda path: state["paths"].get(path, (None, False))[1])

    def identity(path: Path, *, directory: bool = False) -> tuple[int, int]:
        inode, is_directory = state["paths"][path]
        if is_directory != directory:
            raise PermissionError("controlled wrong path kind")
        return inode[0], inode[1]

    def prepared(_root: Path, _binding: Any) -> Any:
        modules["watch_transition"]._validate_record(state["document"], _binding)
        if state["paths"] != {paths.historical_lock: (original, False)}:
            raise PermissionError("controlled unexpected preparation artifact")
        return json.loads(json.dumps(state["document"]))

    def mkdir(path: Path, *, mode: int) -> None:
        assert path == paths.retained_lock and mode == 0o700
        assert path not in state["paths"]
        state["paths"][path] = (barrier, True)

    def write(_path: Path, value: Any, *, before_publish: Any, create_parent: bool) -> None:
        assert create_parent is False
        before_publish()
        state["document"] = json.loads(json.dumps(value))
        state["writes"].append(value["phase"])

    def exchange(first: Path, second: Path) -> None:
        state["paths"][first], state["paths"][second] = state["paths"][second], state["paths"][first]
        state["exchanges"] += 1

    monkeypatch.setattr(module, "_identity", identity)
    monkeypatch.setattr(module, "read_preparation", prepared)
    monkeypatch.setattr(module, "write_private", write)
    state["native_backend"] = module._exchange_backend
    monkeypatch.setattr(module, "_exchange_backend", lambda: exchange)
    monkeypatch.setattr(Path, "mkdir", mkdir)
    lease = SimpleNamespace(lock_path=paths.historical_lock, held=True)

    def held_identity() -> tuple[int, int]:
        if not lease.held or identity(lease.lock_path) != original:
            raise PermissionError("controlled lost native custody")
        return original

    def acquire(*, expected_file_identity: tuple[int, int]) -> None:
        assert identity(lease.lock_path) == expected_file_identity == original
        state["acquisitions"].append(lease.lock_path)
        lease.held = True

    lease.held_file_identity, lease.acquire = held_identity, acquire
    return module, state, root, binding, lease


def _seal(fixture: Any, **kwargs: Any) -> Any:
    module, _state, root, binding, lease = fixture
    return module.seal(
        root, binding, lease, validate=kwargs.get("validate", lambda: None),
        check_deadline=kwargs.get("check_deadline", lambda: None),
    )


def test_sealing_orders_staging_exchange_and_epoch_without_new_authority(sealing: Any) -> None:
    module, state, root, binding, lease = sealing
    result = _seal(sealing)
    paths = module.watch_state_paths(root)
    assert result["phase"] == "sealed-legacy" and result["seal"]["boot"] == BOOT
    assert module.read_sealing(root, binding) == (result, True)
    assert lease.lock_path == paths.retained_lock
    assert state["exchanges"] == 1 and not state["acquisitions"]
    assert state["writes"] == ["prepared", "sealed-legacy"]
    assert state["syncs"] == [paths.retained_lock, root, paths.historical_lock, root]
    assert paths.isolated_subscriptions.parent not in state["paths"]
    assert _seal(sealing) == result
    assert state["exchanges"] == 1 and state["writes"] == ["prepared", "sealed-legacy"]


@pytest.mark.parametrize("boundary", ["staging", "exchange", "exchange-return", "barrier-sync", "epoch"])
def test_interrupted_sealing_recovers_actual_topology_without_reexchange(
    sealing: Any, monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    module, state, root, _binding, lease = sealing
    write, sync, backend = module.write_private, module._sync_directory, module._exchange_backend()

    def publish(path: Path, document: Any, **kwargs: Any) -> None:
        write(path, document, **kwargs)
        if (document["phase"] == "prepared") == (boundary == "staging"):
            raise OSError("controlled publication acknowledgement loss")

    def exchange(first: Path, second: Path) -> None:
        if boundary == "exchange":
            raise OSError("controlled exchange unavailable")
        backend(first, second)
        if boundary == "exchange-return":
            raise OSError("controlled interrupted exchange return")

    def synced(path: Path) -> None:
        sync(path)
        if boundary == "barrier-sync" and path == module.watch_state_paths(root).historical_lock:
            raise OSError("controlled barrier acknowledgement loss")

    with monkeypatch.context() as failed:
        if boundary in ("staging", "epoch"):
            failed.setattr(module, "write_private", publish)
        failed.setattr(module, "_exchange_backend", lambda: exchange)
        failed.setattr(module, "_sync_directory", synced)
        with pytest.raises(OSError, match="controlled"):
            _seal(sealing)
    # A new owner must acquire the one actual original inode, never writer.lock.
    lease.held = False
    lease.lock_path = module.watch_state_paths(root).historical_lock
    assert _seal(sealing)["phase"] == "sealed-legacy"
    assert state["exchanges"] == 1
    assert state["acquisitions"][-1] == (
        module.watch_state_paths(root).historical_lock
        if boundary in ("staging", "exchange") else module.watch_state_paths(root).retained_lock
    )


@pytest.mark.parametrize("change", ["binding", "pin", "barrier", "phase", "boot", "extra", "isolated"])
def test_sealing_refuses_conflicting_record_or_native_evidence_before_acquisition(
    sealing: Any, change: str,
) -> None:
    module, state, root, _binding, lease = sealing
    _seal(sealing)
    lease.held = False
    if change == "binding":
        state["document"]["binding"]["stateRoot"] = "foreign"
    elif change == "pin":
        state["pin"] = (2, 7)
    elif change == "barrier":
        state["document"]["seal"]["barrier"]["inode"] = 7
    elif change == "phase":
        state["document"]["phase"] = "active"
    elif change == "boot":
        state["document"]["seal"]["boot"] = BOOT.replace("linux", "windows")
    elif change == "extra":
        state["document"]["seal"]["paths"] = "foreign"
    else:
        state["paths"][module.watch_state_paths(root).isolated_subscriptions.parent] = ((2, 8), True)
    with pytest.raises(PermissionError):
        _seal(sealing)
    assert state["acquisitions"] == []


@pytest.mark.parametrize("boundary", ["staging", "exchange", "epoch"])
def test_sealing_rechecks_scope_deadline_and_source_at_final_boundaries(
    sealing: Any, monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    module, state, _root, _binding, _lease = sealing
    publish, backend = module.write_private, module._exchange_backend()
    refused = False

    def validate() -> None:
        if refused:
            raise PermissionError("controlled changed scope")

    def changed(path: Path, document: Any, **kwargs: Any) -> None:
        nonlocal refused
        if (document["phase"] == "prepared") == (boundary == "staging"):
            refused = True
        publish(path, document, **kwargs)

    def exchange(first: Path, second: Path) -> None:
        nonlocal refused
        refused = True
        backend(first, second)

    monkeypatch.setattr(module, "write_private", changed)
    if boundary == "exchange":
        monkeypatch.setattr(module, "_exchange_backend", lambda: exchange)
    with pytest.raises(PermissionError, match="changed scope"):
        _seal(sealing, validate=validate)
    assert state["document"]["phase"] == "prepared"


def test_unsupported_backend_refuses_before_staging_or_custody(sealing: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    module, state, _root, _binding, _lease = sealing

    def unsupported() -> Any:
        raise NotImplementedError("controlled unavailable atomic exchange")

    monkeypatch.setattr(module, "_exchange_backend", unsupported)
    with pytest.raises(NotImplementedError):
        _seal(sealing)
    assert state["writes"] == [] and state["exchanges"] == 0 and state["acquisitions"] == []
    assert len(state["paths"]) == 1


def test_unjournaled_barrier_is_retained_and_never_used_as_recovery_authority(sealing: Any) -> None:
    module, state, root, _binding, lease = sealing
    paths = module.watch_state_paths(root)
    state["paths"][paths.retained_lock] = ((2, 4), True)
    lease.held = False
    with pytest.raises(PermissionError, match="unexpected preparation artifact"):
        _seal(sealing)
    assert state["acquisitions"] == [] and state["exchanges"] == 0
    assert paths.retained_lock in state["paths"]


@pytest.mark.parametrize("failure", [False, True])
def test_linux_backend_uses_only_atomic_exchange_and_preserves_native_failure(
    sealing: Any, monkeypatch: pytest.MonkeyPatch, failure: bool,
) -> None:
    module, state, root, _binding, _lease = sealing
    calls: list[tuple[Any, ...]] = []

    class Rename:
        def __call__(self, *args: Any) -> int:
            calls.append(args)
            return -1 if failure else 0

    native_api = ModuleType("ctypes")
    for name in ("c_int", "c_char_p", "c_uint"):
        setattr(native_api, name, object())
    setattr(native_api, "CDLL", lambda *_a, **_kw: SimpleNamespace(renameat2=Rename()))
    setattr(native_api, "get_errno", lambda: 95)
    with monkeypatch.context() as native:
        native.setattr(module.sys, "platform", "linux")
        native.setitem(sys.modules, "ctypes", native_api)
        exchange = state["native_backend"]()
        if failure:
            with pytest.raises(OSError) as error:
                exchange(root / "original", root / "barrier")
            assert error.value.errno == 95
        else:
            exchange(root / "original", root / "barrier")
    assert calls == [(-100, os.fsencode(root / "original"), -100, os.fsencode(root / "barrier"), 2)]
    assert state["writes"] == [] and state["exchanges"] == 0


@pytest.fixture
def writer_boundary(sealing: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    module, state, root, _binding, lease = sealing
    package = module.__package__
    daemon = ModuleType(f"{package}.watch_daemon")
    for name in ("Fetch", "NativeFetch", "Notify", "WatchDaemon"):
        setattr(daemon, name, Any)
    notification = ModuleType(f"{package}.watch_notification")
    setattr(notification, "default_notify", lambda *_a: pytest.fail("unexpected callback"))
    setattr(notification, "positive_seconds", lambda value: value)
    monkeypatch.setitem(sys.modules, daemon.__name__, daemon)
    monkeypatch.setitem(sys.modules, notification.__name__, notification)
    spec = importlib.util.spec_from_file_location(f"{package}.watch_writer", SOURCE / "watch_writer.py")
    assert spec and spec.loader
    writer_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(writer_module)
    owner = writer_module.WatchWriter.__new__(writer_module.WatchWriter)
    owner.root, owner._lease = root, lease
    owner._lifecycle, owner._creating_pid = threading.Lock(), os.getpid()
    owner._read_ready, owner._drain_started = False, True
    owner._preparation_guard, owner._strict_custody, owner._closed = None, True, False
    owner.authorize = lambda _operation: None
    state["persistence_guard"] = None

    def set_guard(guard: Any, **_kwargs: Any) -> None:
        state["persistence_guard"] = guard

    owner.daemon = SimpleNamespace(
        quiescent=True, effects_enabled=False, has_legacy_pending=lambda: False,
        set_persistence_guard=set_guard, stop_guarded_work=lambda: None,
    )
    return owner, state


def test_writer_seals_under_its_existing_custody_and_retains_all_write_fences(writer_boundary: Any) -> None:
    owner, state = writer_boundary
    assert owner.seal_state_transition(timeout=3)["phase"] == "sealed-legacy"
    assert owner.writer_held and not owner.read_ready
    assert state["exchanges"] == 1
    with pytest.raises(PermissionError, match="guarded legacy recovery"):
        state["persistence_guard"]()
    with pytest.raises(RuntimeError, match="read-ready"):
        owner.activate(timeout=3)


@pytest.mark.parametrize("condition", ["active", "undrained", "queued", "authority"])
def test_writer_rejects_unsettled_or_unauthorized_sealing_before_physical_effects(
    writer_boundary: Any, condition: str,
) -> None:
    owner, state = writer_boundary
    if condition == "active":
        owner.daemon.effects_enabled = True
    elif condition == "undrained":
        owner.daemon.quiescent = False
    elif condition == "queued":
        owner.daemon.has_legacy_pending = lambda: True
    else:
        owner.authorize = lambda _operation: False
    with pytest.raises((RuntimeError, PermissionError)):
        owner.seal_state_transition(timeout=3)
    assert state["exchanges"] == 0 and state["writes"] == [] and owner.writer_held


@pytest.mark.parametrize("failure", ["legacy-source", "deadline"])
@pytest.mark.parametrize("phase", ["prepared", "sealed-legacy"])
def test_final_publication_rechecks_legacy_source_and_deadline(
    sealing: Any, monkeypatch: pytest.MonkeyPatch, failure: str, phase: str,
) -> None:
    module, state, _root, _binding, _lease = sealing
    publish = module.write_private
    invalid = False

    def read_source(*_args: Any, **_kwargs: Any) -> list[Any]:
        if invalid and failure == "legacy-source":
            raise PermissionError("controlled corrupt legacy source")
        return []

    def deadline() -> None:
        if invalid and failure == "deadline":
            raise TimeoutError("controlled elapsed deadline")

    def changed(path: Path, document: Any, **kwargs: Any) -> None:
        nonlocal invalid
        if document["phase"] == phase:
            invalid = True
        publish(path, document, **kwargs)

    monkeypatch.setattr(module, "_read_subscriptions_state", read_source)
    monkeypatch.setattr(module, "write_private", changed)
    with pytest.raises((PermissionError, TimeoutError), match="controlled"):
        _seal(sealing, check_deadline=deadline)
    assert state["document"]["phase"] == "prepared"
    assert state["exchanges"] == (phase == "sealed-legacy")


def test_changed_preparation_snapshot_cannot_be_adopted_as_staging_authority(
    sealing: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, state, _root, _binding, _lease = sealing
    read = module._regular_bytes
    reads = 0

    def changed(path: Path) -> bytes:
        nonlocal reads
        reads += 1
        if reads == 3:
            state["document"]["lease"]["inode"] = 7
        value = read(path)
        assert isinstance(value, bytes)
        return value

    monkeypatch.setattr(module, "_regular_bytes", changed)
    with pytest.raises(PermissionError, match="changed before staging"):
        _seal(sealing)
    assert state["writes"] == [] and state["exchanges"] == 0 and len(state["paths"]) == 1


def test_retry_cannot_exchange_until_staged_journal_durability_is_confirmed(
    sealing: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, state, root, _binding, _lease = sealing
    publish = module.write_private

    def unacknowledged(path: Path, document: Any, **kwargs: Any) -> None:
        publish(path, document, **kwargs)
        raise OSError("controlled journal replacement without directory acknowledgement")

    with monkeypatch.context() as interrupted:
        interrupted.setattr(module, "write_private", unacknowledged)
        with pytest.raises(OSError, match="without directory acknowledgement"):
            _seal(sealing)
    paths = module.watch_state_paths(root)
    assert state["document"]["seal"]["boot"] is None
    assert not state["paths"][paths.historical_lock][1]
    assert state["exchanges"] == 0

    def still_uncertain(_path: Path) -> None:
        raise OSError("controlled staged journal still not durable")

    with monkeypatch.context() as uncertain:
        uncertain.setattr(module, "confirm_private", still_uncertain)
        with pytest.raises(OSError, match="still not durable"):
            _seal(sealing)
    assert state["exchanges"] == 0 and not state["paths"][paths.historical_lock][1]
    assert _seal(sealing)["phase"] == "sealed-legacy"
    assert state["exchanges"] == 1
