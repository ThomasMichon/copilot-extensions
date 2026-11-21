"""Actor-free prepared adaptation; native custody remains separately qualified."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from promotion_unit_tests.test_watch_sealing import sealing as sealing
from promotion_unit_tests.test_watch_sealing import windows_sealing as windows_sealing
from promotion_unit_tests.test_watch_sealing import writer_boundary as writer_boundary


def _load_recovery(fixture: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    modeled, state, root, binding, lease = fixture
    package = modeled.__package__
    installation = ModuleType(f"{package}.watch_installation")
    setattr(installation, "WatchInstallation", object)
    monkeypatch.setitem(sys.modules, installation.__name__, installation)
    zdd = ModuleType("zdd")
    zdd.__path__ = []
    cutover = ModuleType("zdd.cutover_lock")
    setattr(cutover, "CutoverLock", object)
    setattr(cutover, "lock_path", lambda root: root / "cutover.lock")
    monkeypatch.setitem(sys.modules, "zdd", zdd)
    monkeypatch.setitem(sys.modules, cutover.__name__, cutover)
    source = Path(modeled.__file__).with_name("watch_transition_recovery.py")
    spec = importlib.util.spec_from_file_location(f"{package}.watch_transition_recovery", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    spec.loader.exec_module(module)
    paths = module.watch_state_paths(root)
    monkeypatch.setattr(module, "require_private", lambda _path: None)
    monkeypatch.setattr(module, "_regular_bytes", lambda _path: json.dumps(state["document"]).encode())
    monkeypatch.setattr(
        module, "_historical_identity", lambda _root: modeled._identity(paths.historical_lock),
    )
    monkeypatch.setattr(module, "_pin_identity", lambda _root: state["pin"])
    monkeypatch.setattr(module, "_confirm_pin", lambda _root, _identity: None)
    monkeypatch.setattr(module, "_read_subscriptions_state", lambda _path, **_kw: [])
    monkeypatch.setattr(module, "write_private", modeled.write_private)
    state["confirmations"] = []
    monkeypatch.setattr(module, "confirm_private", lambda path: state["confirmations"].append(path))
    lease.held = False
    return module, state, root, binding, lease


@pytest.fixture
def recovery(sealing: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    return _load_recovery(sealing, monkeypatch)


@pytest.fixture
def sealed_recovery(windows_sealing: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    windows, state, root, binding, lease = windows_sealing
    state["prepared_origin"] = json.loads(json.dumps(state["document"]))
    publish = windows.write_private

    def capture(*args: Any, **kwargs: Any) -> None:
        publish(*args, **kwargs)
        if state["document"]["phase"] == "prepared":
            state["staged_origin"] = json.loads(json.dumps(state["document"]))

    monkeypatch.setattr(windows, "write_private", capture)
    windows.seal(root, binding, lease, validate=lambda: None, check_deadline=lambda: None)
    state["writes"].clear()
    state["acquisitions"].clear()
    return _load_recovery(windows_sealing, monkeypatch)


def _recover(fixture: Any) -> Any:
    module, _state, root, _binding, lease = fixture
    return module.recover_prepared(
        root, lease, installation=None, guard=None,
        validate=lambda: None, check_deadline=lambda: None,
    )


@pytest.fixture
def recovered_sealing(windows_sealing: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    fixture = _load_recovery(windows_sealing, monkeypatch)
    _recover(fixture)
    fixture[1]["writes"].clear()
    fixture[1]["acquisitions"].clear()
    return fixture


def _seal_recovered(fixture: Any, **kwargs: Any) -> Any:
    module, _state, root, _binding, lease = fixture
    return module.seal_recovered(
        root, lease, installation=None, guard=None,
        validate=kwargs.get("validate", lambda: None),
        check_deadline=kwargs.get("check_deadline", lambda: None),
    )


def test_recovered_sealing_preserves_full_origin_and_reuses_windows_owner(
    recovered_sealing: Any,
) -> None:
    module, state, root, _binding, lease = recovered_sealing
    origin = json.loads(json.dumps(state["document"]["origin"]))
    result = _seal_recovered(recovered_sealing)
    paths = module.watch_state_paths(root)
    assert result["version"] == 2 and result["phase"] == "sealed-legacy"
    assert result["origin"] == origin and result["origin"]["phase"] == "prepared"
    assert all(result[field] == origin[field] for field in ("schema", "binding", "lease"))
    assert state["writes"] == ["prepared", "sealed-legacy"]
    assert state["moves"] == [
        (paths.historical_lock, paths.retained_lock),
        (paths.staged_barrier, paths.historical_lock),
    ]
    assert state["releases"] == 1 and state["acquisitions"] == [paths.writer_lock]
    assert lease.held and lease.lock_path == paths.writer_lock
    assert paths.isolated_subscriptions.parent not in state["paths"]
    serialized = json.dumps(state["document"])
    assert _seal_recovered(recovered_sealing) == result
    assert json.dumps(state["document"]) == serialized
    with pytest.raises(PermissionError):
        sys.modules[f"{module.__package__}.watch_windows_sealing"].read_sealing(root, origin["binding"])


@pytest.mark.parametrize("boundary", ["staging", "release", "move", "barrier", "acquisition", "sealed"])
def test_recovered_sealing_resumes_each_existing_windows_crash_window(
    recovered_sealing: Any, monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    module, state, root, _binding, lease = recovered_sealing
    windows = sys.modules[f"{module.__package__}.watch_windows_sealing"]
    origin = json.loads(json.dumps(state["document"]["origin"]))
    paths = module.watch_state_paths(root)
    publish, move = windows.write_private, windows.windows_move_no_replace
    release, acquire = lease.release, windows._acquire_barrier_lease

    def interrupted_write(path: Path, document: Any, **kwargs: Any) -> None:
        publish(path, document, **kwargs)
        if (document["phase"] == "prepared") == (boundary == "staging"):
            raise OSError("controlled v2 publication interruption")

    def interrupted_move(source: Path, destination: Path) -> None:
        move(source, destination)
        if destination == (paths.retained_lock if boundary == "move" else paths.historical_lock):
            raise OSError("controlled v2 move interruption")

    def interrupted_release() -> None:
        release()
        raise OSError("controlled v2 release interruption")

    def interrupted_acquire(*args: Any) -> None:
        acquire(*args)
        raise OSError("controlled v2 acquisition interruption")

    with monkeypatch.context() as failed:
        if boundary in ("staging", "sealed"):
            failed.setattr(windows, "write_private", interrupted_write)
        elif boundary == "release":
            failed.setattr(lease, "release", interrupted_release)
        elif boundary == "acquisition":
            failed.setattr(windows, "_acquire_barrier_lease", interrupted_acquire)
        else:
            failed.setattr(windows, "windows_move_no_replace", interrupted_move)
        with pytest.raises(OSError, match="controlled v2"):
            _seal_recovered(recovered_sealing)
    lease.held, lease.lock_path = False, paths.historical_lock
    result = _seal_recovered(recovered_sealing)
    assert result["phase"] == "sealed-legacy" and result["origin"] == origin
    assert len(state["moves"]) == 2 and paths.retained_lock not in state["acquisitions"]
    assert lease.held and lease.lock_path == paths.writer_lock
    serialized = json.dumps(state["document"])
    assert _recover_sealed(recovered_sealing) == result
    assert json.dumps(state["document"]) == serialized


@pytest.mark.parametrize("change", ["origin", "schema", "binding", "lease", "controller", "phase", "extra"])
def test_recovered_sealing_refuses_changed_provenance_before_any_physical_step(
    recovered_sealing: Any, change: str,
) -> None:
    _module, state, _root, _binding, lease = recovered_sealing
    lease.held = False
    if change == "origin":
        state["document"]["origin"]["unexpected"] = True
    elif change == "schema":
        state["document"]["schema"] += "-foreign"
    elif change in ("binding", "controller"):
        state["document"][change] = {**state["document"][change], "stateRoot": "foreign"}
    elif change == "lease":
        state["document"]["lease"] = {"device": 2, "inode": 8}
    elif change == "phase":
        state["document"]["phase"] = "import-ready"
    else:
        state["document"]["unexpected"] = True
    prior = json.dumps(state["document"])
    with pytest.raises(PermissionError):
        _seal_recovered(recovered_sealing)
    assert json.dumps(state["document"]) == prior
    assert not lease.held and state["writes"] == [] and state["moves"] == []
    assert state["acquisitions"] == []


@pytest.mark.parametrize("boundary", ["staging", "move", "barrier", "acquisition", "sealed"])
def test_recovered_sealing_rechecks_admission_at_each_existing_boundary(
    recovered_sealing: Any, monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    module, state, root, _binding, _lease = recovered_sealing
    windows = sys.modules[f"{module.__package__}.watch_windows_sealing"]
    paths = module.watch_state_paths(root)
    publish, move = windows.write_private, windows.windows_move_no_replace
    acquire = windows._acquire_barrier_lease
    denied = False

    def validate() -> None:
        if denied:
            raise PermissionError("controlled v2 admission loss")

    def guarded_write(path: Path, document: Any, **kwargs: Any) -> None:
        nonlocal denied
        denied = (document["phase"] == "prepared") == (boundary == "staging")
        publish(path, document, **kwargs)

    def guarded_move(source: Path, destination: Path) -> None:
        nonlocal denied
        move(source, destination)
        denied = destination == (paths.retained_lock if boundary == "move" else paths.historical_lock)

    def guarded_acquire(*args: Any) -> None:
        nonlocal denied
        acquire(*args)
        denied = True

    if boundary in ("staging", "sealed"):
        monkeypatch.setattr(windows, "write_private", guarded_write)
    elif boundary == "acquisition":
        monkeypatch.setattr(windows, "_acquire_barrier_lease", guarded_acquire)
    else:
        monkeypatch.setattr(windows, "windows_move_no_replace", guarded_move)
    with pytest.raises(PermissionError, match="controlled v2 admission"):
        _seal_recovered(recovered_sealing, validate=validate)
    assert state["document"]["phase"] == "prepared"
    assert len(state["moves"]) == (0 if boundary == "staging" else 1 if boundary == "move" else 2)


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_recovered_sealing_refuses_unqualified_posix_before_custody(
    recovered_sealing: Any, monkeypatch: pytest.MonkeyPatch, platform: str,
) -> None:
    _module, state, _root, _binding, lease = recovered_sealing
    lease.held = False
    monkeypatch.setattr(sys, "platform", platform)
    with pytest.raises(NotImplementedError, match="domain"):
        _seal_recovered(recovered_sealing)
    assert not lease.held and state["writes"] == [] and state["moves"] == []
    assert state["acquisitions"] == []


def test_recovered_sealing_writer_preserves_all_existing_effect_fences(
    recovered_sealing: Any, writer_boundary: Any,
) -> None:
    _module, state, _root, _binding, _lease = recovered_sealing
    owner, _ = writer_boundary
    origin = json.loads(json.dumps(state["document"]["origin"]))
    result = owner.seal_recovered_state_transition(timeout=3)
    assert result["origin"] == origin and result["phase"] == "sealed-legacy"
    assert owner.writer_held and not owner.read_ready
    assert owner.daemon.quiescent and not owner.daemon.effects_enabled
    with pytest.raises(PermissionError, match="does not authorize legacy writes"):
        state["persistence_guard"]()
    with pytest.raises(RuntimeError, match="read-ready"):
        owner.activate(timeout=3)
    with pytest.raises(PermissionError):
        owner.seal_state_transition(timeout=3)


def test_recovered_sealing_validates_legacy_source_before_native_acquisition(
    recovered_sealing: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, state, _root, _binding, lease = recovered_sealing
    lease.held = False

    def invalid(*_args: Any, **_kwargs: Any) -> Any:
        raise ValueError("controlled invalid legacy source")

    monkeypatch.setattr(module, "_read_subscriptions_state", invalid)
    with pytest.raises(ValueError, match="controlled invalid"):
        _seal_recovered(recovered_sealing)
    assert state["acquisitions"] == [] and state["moves"] == [] and state["writes"] == []


def test_recovered_sealing_cannot_downgrade_actual_sealed_origin(sealed_recovery: Any) -> None:
    _module, state, _root, binding, lease = sealed_recovery
    origin = json.loads(json.dumps(state["document"]))
    state["document"].update(version=2, phase="prepared", origin=origin, controller=binding)
    prior = json.dumps(state["document"])
    with pytest.raises(PermissionError, match="downgrade"):
        _seal_recovered(sealed_recovery)
    assert not lease.held and state["acquisitions"] == [] and state["writes"] == []
    assert json.dumps(state["document"]) == prior


def test_recovered_sealing_preserves_original_staged_v1_receipt(
    windows_sealing: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    windows, state, root, binding, lease = windows_sealing
    publish = windows.write_private

    def interrupted(*args: Any, **kwargs: Any) -> None:
        publish(*args, **kwargs)
        raise OSError("controlled v1 staging acknowledgement loss")

    with monkeypatch.context() as failed:
        failed.setattr(windows, "write_private", interrupted)
        with pytest.raises(OSError, match="controlled v1 staging"):
            windows.seal(root, binding, lease, validate=lambda: None, check_deadline=lambda: None)
    origin = json.loads(json.dumps(state["document"]))
    state["document"].update(version=2, origin=origin, controller=binding)
    fixture = _load_recovery(windows_sealing, monkeypatch)
    result = _seal_recovered(fixture)
    assert result["origin"] == origin and result["seal"] == origin["seal"]
    assert result["phase"] == "sealed-legacy" and len(state["moves"]) == 2
    assert fixture[0].watch_state_paths(root).retained_lock not in state["acquisitions"]


@pytest.mark.parametrize("overflow", ["seal", "phase"])
def test_recovered_sealing_preflights_both_envelopes_before_changing_topology(
    recovered_sealing: Any, monkeypatch: pytest.MonkeyPatch, overflow: str,
) -> None:
    module, state, _root, _binding, lease = recovered_sealing
    windows = sys.modules[f"{module.__package__}.watch_windows_sealing"]
    if overflow == "phase":
        publish = windows.write_private

        def staged(*args: Any, **kwargs: Any) -> None:
            publish(*args, **kwargs)
            raise OSError("controlled v2 staging acknowledgement loss")

        with monkeypatch.context() as failed:
            failed.setattr(windows, "write_private", staged)
            with pytest.raises(OSError, match="controlled v2 staging"):
                _seal_recovered(recovered_sealing)
    lease.held = False
    state["writes"].clear()
    state["acquisitions"].clear()
    prior = json.dumps(state["document"])
    topology, releases = dict(state["paths"]), state["releases"]
    # This valid prepared envelope fits exactly; its seal or final phase does not.
    monkeypatch.setattr(windows, "_MAX_METADATA_BYTES", len(prior.encode("utf-8")))
    with pytest.raises(ValueError, match="bounded size"):
        _seal_recovered(recovered_sealing)
    assert state["paths"] == topology and state["releases"] == releases
    assert json.dumps(state["document"]) == prior
    assert not lease.held and state["acquisitions"] == [] and state["writes"] == []
    assert state["moves"] == []


def test_prepared_adapter_preserves_complete_origin(recovery: Any) -> None:
    _module, state, _root, binding, lease = recovery
    original = json.loads(json.dumps(state["document"]))
    result = _recover(recovery)
    assert result["version"] == 2 and result["phase"] == "prepared"
    assert result["origin"] == original
    assert result["binding"] == original["binding"] and result["lease"] == original["lease"]
    assert result["controller"] == binding
    assert lease.held and len(state["acquisitions"]) == 1
    assert state["writes"] == ["prepared"]


def test_prepared_adapter_final_guard_preserves_concurrent_journal(
    recovery: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, state, _root, _binding, lease = recovery
    publish = module.write_private

    def raced(*args: Any, **kwargs: Any) -> None:
        state["document"]["unexpected"] = True
        publish(*args, **kwargs)

    monkeypatch.setattr(module, "write_private", raced)
    with pytest.raises(PermissionError):
        _recover(recovery)
    assert state["document"]["version"] == 1 and state["document"]["unexpected"] is True
    assert state["writes"] == [] and lease.held


def test_prepared_adapter_current_retry_confirms_without_rewriting(recovery: Any) -> None:
    _module, state, root, _binding, lease = recovery
    result = _recover(recovery)
    serialized = json.dumps(state["document"])
    lease.held = False
    assert _recover(recovery) == result
    assert json.dumps(state["document"]) == serialized
    assert state["writes"] == ["prepared"]
    assert state["confirmations"] == [root / "watch-state-transition.json"]
    assert len(state["acquisitions"]) == 2


def _recover_sealed(fixture: Any, **kwargs: Any) -> Any:
    module, _state, root, _binding, lease = fixture
    return module.recover_sealed(
        root, lease, installation=None, guard=None,
        validate=kwargs.get("validate", lambda: None),
        check_deadline=kwargs.get("check_deadline", lambda: None),
    )


def test_sealed_adapter_preserves_actual_origin_and_only_acquires_barrier(sealed_recovery: Any) -> None:
    module, state, root, binding, lease = sealed_recovery
    original = json.loads(json.dumps(state["document"]))
    result = _recover_sealed(sealed_recovery)
    assert result["version"] == 2 and result["phase"] == "sealed-legacy"
    assert result["origin"] == original
    assert all(result[field] == original[field] for field in ("binding", "lease", "seal"))
    assert result["controller"] == binding
    assert state["acquisitions"] == [module.watch_state_paths(root).writer_lock]
    assert lease.held and state["writes"] == ["sealed-legacy"]
    assert len(state["moves"]) == 2 and state["releases"] == 1
    assert module.watch_state_paths(root).isolated_subscriptions.parent not in state["paths"]


def test_sealed_adapter_retry_confirms_without_rewriting_or_reopening_retained(sealed_recovery: Any) -> None:
    module, state, root, _binding, lease = sealed_recovery
    result = _recover_sealed(sealed_recovery)
    prior = json.dumps(state["document"])
    lease.held = False
    lease.lock_path = module.watch_state_paths(root).historical_lock
    assert _recover_sealed(sealed_recovery) == result
    assert json.dumps(state["document"]) == prior
    assert state["writes"] == ["sealed-legacy"]
    assert state["confirmations"] == [module.watch_state_paths(root).journal]
    assert state["acquisitions"] == [module.watch_state_paths(root).writer_lock] * 2


@pytest.mark.parametrize("origin_kind", ["prepared_origin", "staged_origin"])
def test_sealed_phase_progression_retains_actual_prepared_origin(
    sealed_recovery: Any, origin_kind: str,
) -> None:
    module, state, root, binding, lease = sealed_recovery
    origin = state[origin_kind]
    state["document"].update(version=2, origin=origin, controller=binding)
    prior = json.dumps(state["document"])
    result = _recover_sealed(sealed_recovery)
    assert result["phase"] == "sealed-legacy" and result["origin"] == origin
    assert result["origin"]["phase"] == "prepared"
    assert json.dumps(state["document"]) == prior and state["writes"] == []
    assert lease.held and state["acquisitions"] == [module.watch_state_paths(root).writer_lock]
    assert state["confirmations"] == [module.watch_state_paths(root).journal]
    with pytest.raises(PermissionError):
        sys.modules[f"{module.__package__}.watch_windows_sealing"].read_sealing(root, binding)
    lease.held = False
    assert _recover_sealed(sealed_recovery) == result
    assert json.dumps(state["document"]) == prior


@pytest.mark.parametrize("change", [
    "origin-version", "origin-phase", "origin-schema", "origin-extra", "origin-seal",
    "current-schema", "current-binding", "current-lease", "current-phase", "current-seal",
])
def test_sealed_phase_progression_refuses_changed_origin_or_unqualified_current_evidence(
    sealed_recovery: Any, change: str,
) -> None:
    _module, state, _root, binding, lease = sealed_recovery
    origin = state["staged_origin"]
    state["document"].update(version=2, origin=origin, controller=binding)
    if change == "origin-version":
        origin["version"] = 2
    elif change == "origin-phase":
        origin["phase"] = "import-ready"
    elif change == "origin-schema":
        origin["schema"] += "-foreign"
    elif change == "origin-extra":
        origin["unexpected"] = True
    elif change == "origin-seal":
        origin["seal"]["barrier"]["inode"] = 8
    elif change == "current-schema":
        state["document"]["schema"] += "-foreign"
    elif change == "current-binding":
        state["document"]["binding"] = {**binding, "stateRoot": str(_root) + "-foreign"}
    elif change == "current-lease":
        state["document"]["lease"]["inode"] = 8
    elif change == "current-phase":
        state["document"]["phase"] = "prepared"
    else:
        state["document"]["seal"]["boot"] = "unqualified-domain"
    prior = json.dumps(state["document"])
    with pytest.raises(PermissionError):
        _recover_sealed(sealed_recovery)
    assert not lease.held and state["acquisitions"] == [] and state["writes"] == []
    assert json.dumps(state["document"]) == prior


@pytest.mark.parametrize("change", [
    "binding", "pin", "barrier", "retained", "staged", "isolated", "phase", "boot", "extra", "seal",
])
def test_sealed_adapter_refuses_invalid_origin_or_topology_before_custody(
    sealed_recovery: Any, change: str,
) -> None:
    module, state, root, _binding, lease = sealed_recovery
    paths = module.watch_state_paths(root)
    if change == "binding":
        state["document"]["binding"]["stateRoot"] += "-foreign"
    elif change == "pin":
        state["pin"] = (2, 8)
    elif change == "barrier":
        state["paths"][paths.historical_lock] = ((2, 8), True)
    elif change == "retained":
        state["paths"][paths.retained_lock] = ((2, 8), False)
    elif change == "staged":
        state["paths"][paths.staged_barrier] = ((2, 8), True)
    elif change == "isolated":
        state["paths"][paths.isolated_subscriptions.parent] = ((2, 8), True)
    elif change == "phase":
        state["document"]["phase"] = "prepared"
    elif change == "boot":
        state["document"]["seal"]["boot"] = "linux:2ad542a5-ab10-4ffa-9f4f-9f34955e03b0"
    elif change == "seal":
        del state["document"]["seal"]
    else:
        state["document"]["unexpected"] = True
    prior = json.dumps(state["document"])
    with pytest.raises(PermissionError):
        _recover_sealed(sealed_recovery)
    assert not lease.held and state["acquisitions"] == [] and state["writes"] == []
    assert json.dumps(state["document"]) == prior


@pytest.mark.parametrize("change", ["origin", "seal", "controller", "extra"])
def test_sealed_version_two_rejects_tampered_origin_and_controller(
    sealed_recovery: Any, change: str,
) -> None:
    _module, state, _root, _binding, lease = sealed_recovery
    _recover_sealed(sealed_recovery)
    lease.held = False
    state["acquisitions"].clear()
    state["writes"].clear()
    if change == "origin":
        state["document"]["origin"]["unexpected"] = True
    elif change == "seal":
        state["document"]["seal"]["barrier"]["inode"] = 8
    elif change == "controller":
        state["document"]["controller"]["stateRoot"] += "-foreign"
    else:
        state["document"]["unexpected"] = True
    prior = json.dumps(state["document"])
    with pytest.raises(PermissionError):
        _recover_sealed(sealed_recovery)
    assert not lease.held and state["acquisitions"] == [] and state["writes"] == []
    assert json.dumps(state["document"]) == prior


@pytest.mark.parametrize("change", ["admission", "custody", "journal", "topology", "source", "deadline"])
def test_sealed_adapter_final_write_guard_rechecks_all_evidence(
    sealed_recovery: Any, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    module, state, root, _binding, lease = sealed_recovery
    paths = module.watch_state_paths(root)
    publish = module.write_private
    publishing = False

    def validate() -> None:
        if publishing and change == "admission":
            raise PermissionError("controlled admission loss")

    def deadline() -> None:
        if publishing and change == "deadline":
            raise TimeoutError("controlled deadline")

    def source(*_args: Any, **_kwargs: Any) -> list[Any]:
        if publishing and change == "source":
            raise PermissionError("controlled source loss")
        return []

    def raced(*args: Any, **kwargs: Any) -> None:
        nonlocal publishing
        publishing = True
        if change == "custody":
            lease.held = False
        elif change == "journal":
            state["document"]["unexpected"] = True
        elif change == "topology":
            state["paths"][paths.historical_lock] = ((2, 8), True)
        publish(*args, **kwargs)

    monkeypatch.setattr(module, "write_private", raced)
    monkeypatch.setattr(module, "_read_subscriptions_state", source)
    with pytest.raises((PermissionError, TimeoutError)):
        _recover_sealed(sealed_recovery, validate=validate, check_deadline=deadline)
    assert state["document"]["version"] == 1 and state["writes"] == []


def test_sealed_adapter_uncertain_publication_retains_origin_and_legacy_authority(
    sealed_recovery: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, state, root, _binding, lease = sealed_recovery
    original = json.loads(json.dumps(state["document"]))
    publish = module.write_private

    def uncertain(*args: Any, **kwargs: Any) -> None:
        publish(*args, **kwargs)
        raise OSError("controlled unknown publication")

    with monkeypatch.context() as failed:
        failed.setattr(module, "write_private", uncertain)
        with pytest.raises(OSError, match="unknown publication"):
            _recover_sealed(sealed_recovery)
    serialized = json.dumps(state["document"])
    assert lease.held
    assert _recover_sealed(sealed_recovery)["origin"] == original
    assert json.dumps(state["document"]) == serialized
    assert state["writes"] == ["sealed-legacy"]
    assert module.watch_state_paths(root).isolated_subscriptions.parent not in state["paths"]


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_sealed_adapter_never_retrofits_posix_domain_evidence(
    sealed_recovery: Any, monkeypatch: pytest.MonkeyPatch, platform: str,
) -> None:
    _module, state, _root, _binding, lease = sealed_recovery
    state["document"]["seal"]["backend"] = "linux-rename-exchange"
    state["document"]["seal"]["boot"] = "linux:2ad542a5-ab10-4ffa-9f4f-9f34955e03b0"
    prior = json.dumps(state["document"])
    monkeypatch.setattr(sys, "platform", platform)
    with pytest.raises(NotImplementedError, match="domain"):
        _recover_sealed(sealed_recovery)
    assert json.dumps(state["document"]) == prior
    assert not lease.held and state["writes"] == [] and state["acquisitions"] == []


def test_sealed_writer_receives_under_existing_owner_without_adopting_effects(
    sealed_recovery: Any, writer_boundary: Any,
) -> None:
    _module, state, _root, _binding, _lease = sealed_recovery
    owner, _ = writer_boundary
    origin = json.loads(json.dumps(state["document"]))
    result = owner.recover_sealed_state_transition(timeout=3)
    assert result["origin"] == origin
    assert owner.writer_held and not owner.read_ready
    assert owner.daemon.quiescent and not owner.daemon.effects_enabled
    with pytest.raises(PermissionError, match="does not authorize legacy writes"):
        state["persistence_guard"]()
    with pytest.raises(RuntimeError, match="read-ready"):
        owner.activate(timeout=3)
    with pytest.raises(PermissionError):
        owner.seal_state_transition(timeout=3)
    assert state["writes"] == ["sealed-legacy"]


@pytest.mark.parametrize("condition", ["active", "undrained", "queued", "authority"])
def test_sealed_writer_refuses_before_mutating_custody_or_daemon(
    sealed_recovery: Any, writer_boundary: Any, condition: str,
) -> None:
    _module, state, _root, _binding, lease = sealed_recovery
    owner, _ = writer_boundary
    owner._strict_custody = False
    if condition == "active":
        owner.daemon.effects_enabled = True
    elif condition == "undrained":
        owner.daemon.quiescent = False
    elif condition == "queued":
        owner.daemon.has_legacy_pending = lambda: True
    else:
        owner.authorize = lambda _operation: False
    prior = json.dumps(state["document"])
    with pytest.raises((PermissionError, RuntimeError)):
        owner.recover_sealed_state_transition(timeout=3)
    assert not lease.held and not owner._strict_custody
    assert state["writes"] == [] and state["acquisitions"] == []
    assert state["persistence_guard"] is None
    assert json.dumps(state["document"]) == prior
