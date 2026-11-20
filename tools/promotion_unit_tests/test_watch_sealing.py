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
    monkeypatch.setattr(sys, "platform", "linux")
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


@pytest.fixture
def windows_sealing(sealing: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    linux, state, root, binding, lease = sealing
    package = linux.__package__
    private = sys.modules[f"{package}.watch_private"]
    setattr(private, "_windows_private", lambda *_a, **_kw: None)
    setattr(private, "windows_move_no_replace", lambda *_a: pytest.fail("unselected native move"))
    spec = importlib.util.spec_from_file_location(
        f"{package}.watch_windows_sealing", SOURCE / "watch_windows_sealing.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "platform", "win32")
    paths = module.watch_state_paths(root)
    original, barrier, writer = (2, 3), (2, 4), (2, 5)
    state.update(moves=[], releases=0, new_leases=0)

    def identity(
        path: Path, *, directory: bool = False, installed: bool = False,
    ) -> tuple[int, int]:
        inode, is_directory = state["paths"][path]
        if directory != is_directory:
            raise PermissionError("controlled wrong Windows path kind")
        return inode[0], inode[1]

    def mkdir(path: Path, *, mode: int) -> None:
        assert path == paths.staged_barrier and mode == 0o700
        assert path not in state["paths"]
        state["paths"][path] = (barrier, True)

    def move(source: Path, destination: Path) -> None:
        assert not lease.held
        if destination in state["paths"]:
            raise FileExistsError("controlled raced Windows path")
        state["paths"][destination] = state["paths"].pop(source)
        state["moves"].append((source, destination))

    def release() -> None:
        lease.held = False
        state["releases"] += 1

    def acquire_barrier(_root: Path, document: Any, selected: Any) -> None:
        assert identity(paths.historical_lock, directory=True) == barrier
        assert identity(paths.retained_lock) == original
        if selected.held:
            assert selected.lock_path == paths.writer_lock
            return
        state["paths"][paths.writer_lock] = (writer, False)
        selected.lock_path, selected.held = paths.writer_lock, True
        state["acquisitions"].append(paths.writer_lock)
        state["new_leases"] += 1

    def held_identity() -> tuple[int, int]:
        if not lease.held:
            raise PermissionError("controlled missing Windows lease")
        return identity(lease.lock_path)

    for name in (
        "require_private", "_regular_bytes", "_pin_identity", "_read_subscriptions_state",
        "confirm_private", "write_private", "read_preparation",
    ):
        monkeypatch.setattr(module, name, getattr(linux, name))
    monkeypatch.setattr(module, "_identity", identity)
    state["native_acquire_barrier"] = module._acquire_barrier_lease
    monkeypatch.setattr(module, "_acquire_barrier_lease", acquire_barrier)
    monkeypatch.setattr(module, "windows_move_no_replace", move)
    monkeypatch.setattr(Path, "mkdir", mkdir)
    lease.release, lease.held_file_identity = release, held_identity
    return module, state, root, binding, lease


def test_windows_sealing_releases_before_move_and_only_then_acquires_new_lease(windows_sealing: Any) -> None:
    module, state, root, binding, lease = windows_sealing
    record = _seal(windows_sealing)
    paths = module.watch_state_paths(root)
    assert record["phase"] == "sealed-legacy" and record["seal"]["boot"] is None
    assert module.read_sealing(root, binding) == (record, "sealed")
    assert state["releases"] == 1 and state["new_leases"] == 1
    assert state["moves"] == [
        (paths.historical_lock, paths.retained_lock),
        (paths.staged_barrier, paths.historical_lock),
    ]
    assert lease.lock_path == paths.writer_lock
    assert paths.isolated_subscriptions.parent not in state["paths"]
    assert _seal(windows_sealing) == record
    assert len(state["moves"]) == 2 and state["new_leases"] == 1


@pytest.mark.parametrize("boundary", ["staging", "release", "move", "barrier", "sealed"])
def test_windows_interruption_recovers_without_opening_retained_inode(
    windows_sealing: Any, monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    module, state, root, _binding, lease = windows_sealing
    publish, move, release = module.write_private, module.windows_move_no_replace, lease.release
    paths = module.watch_state_paths(root)

    def interrupted_write(path: Path, document: Any, **kwargs: Any) -> None:
        publish(path, document, **kwargs)
        if (document["phase"] == "prepared") == (boundary == "staging"):
            raise OSError("controlled Windows publication interruption")

    def interrupted_move(source: Path, destination: Path) -> None:
        move(source, destination)
        if destination == (paths.retained_lock if boundary == "move" else paths.historical_lock):
            raise OSError("controlled Windows move interruption")

    def interrupted_release() -> None:
        release()
        raise OSError("controlled Windows release interruption")

    with monkeypatch.context() as interrupted:
        if boundary in ("staging", "sealed"):
            interrupted.setattr(module, "write_private", interrupted_write)
        elif boundary == "release":
            interrupted.setattr(lease, "release", interrupted_release)
        else:
            interrupted.setattr(module, "windows_move_no_replace", interrupted_move)
        with pytest.raises(OSError, match="controlled Windows"):
            _seal(windows_sealing)
    lease.held, lease.lock_path = False, paths.historical_lock
    assert _seal(windows_sealing)["phase"] == "sealed-legacy"
    assert len(state["moves"]) == 2
    assert paths.retained_lock not in state["acquisitions"]


def test_windows_raced_cold_creation_blocks_without_retained_recovery_lease(
    windows_sealing: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, state, root, _binding, lease = windows_sealing
    paths = module.watch_state_paths(root)
    move = module.windows_move_no_replace

    def raced(source: Path, destination: Path) -> None:
        move(source, destination)
        if destination == paths.retained_lock:
            state["paths"][paths.historical_lock] = ((2, 8), False)

    monkeypatch.setattr(module, "windows_move_no_replace", raced)
    with pytest.raises(PermissionError, match="raced historical"):
        _seal(windows_sealing)
    assert not lease.held and state["new_leases"] == 0
    assert state["paths"][paths.historical_lock] == ((2, 8), False)
    lease.lock_path = paths.historical_lock
    with pytest.raises(PermissionError, match="raced historical"):
        _seal(windows_sealing)
    assert state["acquisitions"] == [] and len(state["moves"]) == 1


def test_windows_open_original_handle_refuses_move_without_installing_barrier(
    windows_sealing: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, state, root, _binding, lease = windows_sealing

    def sharing_violation(*_args: Any) -> None:
        raise PermissionError("controlled Windows sharing violation")

    monkeypatch.setattr(module, "windows_move_no_replace", sharing_violation)
    with pytest.raises(PermissionError, match="sharing violation"):
        _seal(windows_sealing)
    paths = module.watch_state_paths(root)
    assert not lease.held and state["new_leases"] == 0
    assert state["paths"][paths.historical_lock] == ((2, 3), False)
    assert paths.retained_lock not in state["paths"]
    assert state["document"]["phase"] == "prepared"


@pytest.mark.parametrize("boundary", ["move", "barrier", "publication"])
def test_windows_rechecks_scope_before_moves_and_final_publication(
    windows_sealing: Any, monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    module, state, root, _binding, lease = windows_sealing
    paths = module.watch_state_paths(root)
    release, move, publish = lease.release, module.windows_move_no_replace, module.write_private
    invalid = False

    def validate() -> None:
        if invalid:
            raise PermissionError("controlled Windows scope change")

    def changed_release() -> None:
        nonlocal invalid
        release()
        if boundary == "move":
            invalid = True

    def changed_move(source: Path, destination: Path) -> None:
        nonlocal invalid
        move(source, destination)
        if boundary == "barrier" and destination == paths.retained_lock:
            invalid = True

    def changed_publish(path: Path, document: Any, **kwargs: Any) -> None:
        nonlocal invalid
        if boundary == "publication" and document["phase"] == "sealed-legacy":
            invalid = True
        publish(path, document, **kwargs)

    monkeypatch.setattr(lease, "release", changed_release)
    monkeypatch.setattr(module, "windows_move_no_replace", changed_move)
    monkeypatch.setattr(module, "write_private", changed_publish)
    with pytest.raises(PermissionError, match="scope change"):
        _seal(windows_sealing, validate=validate)
    assert state["document"]["phase"] == "prepared"
    assert len(state["moves"]) == {"move": 0, "barrier": 1, "publication": 2}[boundary]


@pytest.mark.parametrize("change", ["binding", "pin", "barrier", "phase", "boot", "extra", "isolated"])
def test_windows_recovery_refuses_foreign_or_conflicting_evidence_before_acquiring(
    windows_sealing: Any, change: str,
) -> None:
    module, state, root, _binding, lease = windows_sealing
    _seal(windows_sealing)
    lease.held = False
    acquired = list(state["acquisitions"])
    if change == "binding":
        state["document"]["binding"]["stateRoot"] = "foreign"
    elif change == "pin":
        state["pin"] = (2, 7)
    elif change == "barrier":
        state["document"]["seal"]["barrier"]["inode"] = 7
    elif change == "phase":
        state["document"]["phase"] = "active"
    elif change == "boot":
        state["document"]["seal"]["boot"] = BOOT
    elif change == "extra":
        state["document"]["seal"]["paths"] = "foreign"
    else:
        state["paths"][module.watch_state_paths(root).isolated_subscriptions.parent] = ((2, 8), True)
    with pytest.raises(PermissionError):
        _seal(windows_sealing)
    assert state["acquisitions"] == acquired and len(state["moves"]) == 2


def test_windows_uncertain_staging_refuses_release_until_durability_is_confirmed(
    windows_sealing: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, state, _root, _binding, lease = windows_sealing
    publish = module.write_private

    def unacknowledged(path: Path, document: Any, **kwargs: Any) -> None:
        publish(path, document, **kwargs)
        raise OSError("controlled uncertain Windows staging")

    with monkeypatch.context() as interrupted:
        interrupted.setattr(module, "write_private", unacknowledged)
        with pytest.raises(OSError, match="uncertain Windows staging"):
            _seal(windows_sealing)

    def still_uncertain(_path: Path) -> None:
        raise OSError("controlled Windows journal still uncertain")

    with monkeypatch.context() as uncertain:
        uncertain.setattr(module, "confirm_private", still_uncertain)
        with pytest.raises(OSError, match="still uncertain"):
            _seal(windows_sealing)
    assert lease.held and state["releases"] == 0 and state["moves"] == []
    assert _seal(windows_sealing)["phase"] == "sealed-legacy"


@pytest.mark.parametrize("failure", ["legacy-source", "deadline"])
def test_windows_final_publication_rechecks_source_and_deadline(
    windows_sealing: Any, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    module, state, _root, _binding, _lease = windows_sealing
    publish = module.write_private
    invalid = False

    def read_source(*_args: Any, **_kwargs: Any) -> list[Any]:
        if invalid and failure == "legacy-source":
            raise PermissionError("controlled Windows legacy corruption")
        return []

    def deadline() -> None:
        if invalid and failure == "deadline":
            raise TimeoutError("controlled Windows expired deadline")

    def changed(path: Path, document: Any, **kwargs: Any) -> None:
        nonlocal invalid
        if document["phase"] == "sealed-legacy":
            invalid = True
        publish(path, document, **kwargs)

    monkeypatch.setattr(module, "_read_subscriptions_state", read_source)
    monkeypatch.setattr(module, "write_private", changed)
    with pytest.raises((PermissionError, TimeoutError), match="controlled Windows"):
        _seal(windows_sealing, check_deadline=deadline)
    assert state["document"]["phase"] == "prepared"
    assert state["new_leases"] == 1 and len(state["moves"]) == 2


@pytest.mark.parametrize("error_code", [0, 32, 183])
def test_windows_native_move_preserves_sharing_and_no_replace_semantics(
    windows_sealing: Any, monkeypatch: pytest.MonkeyPatch, error_code: int,
) -> None:
    module, _state, root, _binding, _lease = windows_sealing
    calls: list[tuple[Any, ...]] = []

    class Move:
        def __call__(self, *args: Any) -> int:
            calls.append(args)
            return int(error_code == 0)

    native = ModuleType("ctypes")
    for name in ("c_wchar_p", "c_ulong", "c_int"):
        setattr(native, name, object())
    setattr(native, "WinDLL", lambda *_a, **_kw: SimpleNamespace(MoveFileExW=Move()))
    setattr(native, "get_last_error", lambda: error_code)
    setattr(native, "WinError", lambda code: OSError(code, "controlled native sharing error"))
    persistence = ModuleType("zdd.routing_persistence")
    setattr(persistence, "windows_replace", lambda *_a: pytest.fail("replacement not permitted"))
    monkeypatch.setitem(sys.modules, "ctypes", native)
    monkeypatch.setitem(sys.modules, persistence.__name__, persistence)
    spec = importlib.util.spec_from_file_location(
        f"{module.__package__}.native_private", SOURCE / "watch_private.py",
    )
    assert spec and spec.loader
    private = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(private)
    if error_code == 183:
        with pytest.raises(FileExistsError):
            private.windows_move_no_replace(root / "source", root / "destination")
    elif error_code:
        with pytest.raises(OSError) as error:
            private.windows_move_no_replace(root / "source", root / "destination")
        assert error.value.errno == error_code
    else:
        private.windows_move_no_replace(root / "source", root / "destination")
    assert calls == [(str(root / "source"), str(root / "destination"), 0x8)]


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
