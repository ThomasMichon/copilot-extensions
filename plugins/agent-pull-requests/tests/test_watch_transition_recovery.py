"""Prepared-controller recovery keeps immutable origin and native custody."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from test_watch_installation import _installation, api
from test_watch_runtime import _advance_installation
from zdd.cutover_lock import CutoverLock

from agent_pull_requests import watch_transition
from agent_pull_requests.watch_installation import PLUGIN_ID, WatchInstallation
from agent_pull_requests.watch_private import write_private
from agent_pull_requests.watch_storage import (
    StateCommitUncertain, read_subscriptions_state, read_transition_phase, watch_state_paths,
)
from agent_pull_requests.watch_writer import WatchWriter

pytestmark = pytest.mark.contract("agent_pull_requests.watch.writer_handoff")
Document = dict[str, Any]


def _writer(root: Path) -> WatchWriter:
    return WatchWriter(
        root=root, authorize=lambda _: None, fetch=lambda *_: pytest.fail("must not fetch"),
    )


@pytest.fixture
def prepared(tmp_path: Path) -> tuple[WatchInstallation, WatchInstallation, Document]:
    original = _installation(tmp_path)
    original.state_root.mkdir(parents=True)
    owner = _writer(original.state_root)
    try:
        owner.adopt(timeout=2)
        document = owner.prepare_state_transition(timeout=2, installation=original)
    finally:
        owner.close(timeout=2)
    advance = cast(Callable[[WatchInstallation, Path], WatchInstallation], _advance_installation)
    current = advance(original, tmp_path)
    original.payload_root.rmdir()
    return original, current, document


@pytest.fixture
def controller(prepared: tuple[WatchInstallation, WatchInstallation, Document]) -> Iterator[WatchWriter]:
    owner = _writer(prepared[1].state_root)
    yield owner
    owner.close(timeout=2)


def test_current_controller_adapts_real_old_receipt_without_old_executable(
    prepared: tuple[WatchInstallation, WatchInstallation, Document], controller: WatchWriter,
) -> None:
    old, current, origin = prepared
    paths = watch_state_paths(current.state_root)
    paths.legacy_subscriptions.write_bytes(b" [ ] \n")
    with current.transaction(timeout=2, environment={}) as guard:
        result = controller.recover_prepared_state_transition(
            timeout=10, installation=current, guard=guard,
        )
    assert result["version"] == 2 and result["phase"] == "prepared"
    assert result["origin"] == origin
    assert result["binding"] == origin["binding"]
    assert result["lease"] == origin["lease"]
    assert result["controller"] == {
        "kind": "installation", "stateRoot": str(current.state_root),
        "installation": current.evidence(),
    }
    assert result["binding"]["installation"]["generation"] < current.install_generation
    assert not old.payload_root.exists()
    assert controller.writer_held and not controller.read_ready
    assert controller.daemon.quiescent and not controller.daemon.effects_enabled
    assert paths.legacy_subscriptions.read_bytes() == b" [ ] \n"
    assert not paths.retained_lock.exists() and not paths.isolated_subscriptions.parent.exists()
    assert read_transition_phase(current.state_root) == "prepared"
    with pytest.raises(ValueError, match="guarded recovery"):
        read_subscriptions_state(current.state_root)
    persisted = paths.journal.read_bytes()
    with pytest.raises(PermissionError):
        watch_transition.read_preparation(current.state_root, origin["binding"])
    assert paths.journal.read_bytes() == persisted
    with pytest.raises(RuntimeError, match="read-ready"):
        controller.activate(timeout=2)


def test_version_two_cold_recovery_confirms_without_overwriting_origin(
    prepared: tuple[WatchInstallation, WatchInstallation, Document], controller: WatchWriter,
) -> None:
    _, current, origin = prepared
    with current.transaction(timeout=2, environment={}) as guard:
        controller.recover_prepared_state_transition(timeout=10, installation=current, guard=guard)
    controller.close(timeout=2)
    paths = watch_state_paths(current.state_root)
    original = paths.journal.read_bytes()
    paths.legacy_subscriptions.write_bytes(b"[]\n")
    successor = _writer(current.state_root)
    try:
        with current.transaction(timeout=2, environment={}) as guard:
            result = successor.recover_prepared_state_transition(
                timeout=10, installation=current, guard=guard,
            )
        assert result["origin"] == origin
        assert paths.journal.read_bytes() == original
        assert paths.legacy_subscriptions.read_bytes() == b"[]\n"
        assert successor.writer_held
    finally:
        successor.close(timeout=2)


@pytest.mark.parametrize("change", [
    "sealed", "binding", "origin-extra", "pin", "inode", "retained", "isolated",
    "source", "scope",
])
def test_invalid_prepared_origin_refuses_before_native_acquisition(
    prepared: tuple[WatchInstallation, WatchInstallation, Document], controller: WatchWriter,
    change: str,
) -> None:
    _, current, origin = prepared
    paths = watch_state_paths(current.state_root)
    if change == "sealed":
        origin["phase"] = "sealed-legacy"
    elif change == "binding":
        origin["binding"]["installation"]["sourceFingerprint"] += "-foreign"
    elif change == "origin-extra":
        origin["unexpected"] = True
    elif change == "pin":
        paths.preparation_pin.unlink()
    elif change == "inode":
        paths.historical_lock.unlink()
        paths.historical_lock.write_bytes(b"replacement")
    elif change == "retained":
        paths.retained_lock.write_bytes(b"foreign")
    elif change == "isolated":
        paths.isolated_subscriptions.parent.mkdir()
    elif change == "source":
        paths.legacy_subscriptions.write_bytes(b"[17]")
    elif change == "scope":
        origin["binding"]["stateRoot"] += "-foreign"
    if change in {"sealed", "binding", "origin-extra", "scope"}:
        write_private(paths.journal, origin)
    original = paths.journal.read_bytes()
    with current.transaction(timeout=2, environment={}) as guard:
        with pytest.raises((ValueError, PermissionError)):
            controller.recover_prepared_state_transition(
                timeout=10, installation=current, guard=guard,
            )
    assert not controller.writer_held
    assert paths.journal.read_bytes() == original
    if change == "source":
        assert paths.legacy_subscriptions.read_bytes() == b"[17]"


@pytest.mark.parametrize("custody", ["missing", "unheld", "foreign"])
def test_governed_adapter_requires_actual_current_transaction(
    prepared: tuple[WatchInstallation, WatchInstallation, Document], controller: WatchWriter,
    tmp_path: Path, custody: str,
) -> None:
    _, current, _ = prepared
    guard = CutoverLock(tmp_path / "foreign" if custody == "foreign" else current.run_root)
    if custody == "foreign":
        guard.acquire()
    original = watch_state_paths(current.state_root).journal.read_bytes()
    try:
        with pytest.raises((PermissionError, RuntimeError)):
            controller.recover_prepared_state_transition(
                timeout=2, installation=current, guard=None if custody == "missing" else guard,
            )
    finally:
        guard.release()
    assert not controller.writer_held
    assert watch_state_paths(current.state_root).journal.read_bytes() == original
    assert not controller._strict_custody and controller._preparation_guard is None


@pytest.mark.parametrize("change", ["authority", "native", "journal", "source", "admission"])
def test_adapter_final_write_guard_rechecks_all_custody(
    prepared: tuple[WatchInstallation, WatchInstallation, Document], controller: WatchWriter,
    monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    from agent_pull_requests import watch_transition_recovery

    _, current, _ = prepared
    paths = watch_state_paths(current.state_root)
    persist = watch_transition_recovery.write_private
    original = paths.journal.read_bytes()

    def raced(path: Path, document: Document, **kwargs: Any) -> None:
        if change == "authority":
            def refuse(_operation: str) -> None:
                raise PermissionError("authority changed")
            controller.authorize = refuse
        elif change == "native":
            controller._lease.release()
        elif change == "journal":
            paths.journal.write_bytes(original + b"\n")
        elif change == "source":
            paths.legacy_subscriptions.write_bytes(b"[17]")
        else:
            policy = current.os_profile / ".copilot-extensions" / "installation-mode.json"
            value = json.loads(policy.read_text(encoding="utf-8"))
            value["installationMode"]["enabled"] = False
            policy.write_text(json.dumps(value), encoding="utf-8")
        persist(path, document, **kwargs)

    with current.transaction(timeout=2, environment={}) as guard:
        with monkeypatch.context() as race:
            race.setattr(watch_transition_recovery, "write_private", raced)
            with pytest.raises((PermissionError, ValueError)):
                controller.recover_prepared_state_transition(
                    timeout=10, installation=current, guard=guard,
                )
    assert paths.journal.read_bytes() == original + (b"\n" if change == "journal" else b"")
    assert not controller.read_ready


def test_uncertain_adaptation_reconciles_serialized_version_two_without_rewriting(
    prepared: tuple[WatchInstallation, WatchInstallation, Document], controller: WatchWriter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import watch_transition_recovery

    _, current, origin = prepared
    persist = watch_transition_recovery.write_private

    def uncertain(*args: Any, **kwargs: Any) -> None:
        persist(*args, **kwargs)
        raise StateCommitUncertain("controlled adaptation acknowledgement loss")

    with current.transaction(timeout=2, environment={}) as guard:
        with monkeypatch.context() as failure:
            failure.setattr(watch_transition_recovery, "write_private", uncertain)
            with pytest.raises(StateCommitUncertain):
                controller.recover_prepared_state_transition(
                    timeout=10, installation=current, guard=guard,
                )
        paths = watch_state_paths(current.state_root)
        published = paths.journal.read_bytes()
        assert controller.writer_held and not controller.read_ready
        result = controller.recover_prepared_state_transition(
            timeout=10, installation=current, guard=guard,
        )
    assert result["origin"] == origin
    assert paths.journal.read_bytes() == published


def test_legacy_prepared_adapter_keeps_exact_root_and_original_pin(tmp_path: Path) -> None:
    owner = _writer(tmp_path)
    try:
        owner.adopt(timeout=2)
        original = owner.prepare_state_transition(timeout=2)
        result = owner.recover_prepared_state_transition(timeout=2)
        assert result["origin"] == original
        assert result["controller"] == original["binding"]
        assert owner._lease.held_file_identity() == (
            original["lease"]["device"], original["lease"]["inode"],
        )
    finally:
        owner.close(timeout=2)


def test_later_current_controller_keeps_both_original_generations(
    prepared: tuple[WatchInstallation, WatchInstallation, Document], controller: WatchWriter,
    tmp_path: Path,
) -> None:
    _, current, origin = prepared
    with current.transaction(timeout=2, environment={}) as guard:
        controller.recover_prepared_state_transition(timeout=10, installation=current, guard=guard)
    controller.close(timeout=2)
    payload = tmp_path / "payload-third"
    payload.mkdir()
    stamped = api.stamp_context(
        payload_root=payload, plugin_id=PLUGIN_ID, durable_home=current.durable_home,
        payload_version="3.0.0", payload_origin="explicit",
        source_descriptor={"source": "github", "repo": "example/one"},
        marketplace_key="one", expected_namespace_generation=current.namespace_generation,
        expected_install_generation=current.install_generation, environment={},
    )
    activated = api.compare_and_swap_activation(
        context=current.receipt, durable_home=current.durable_home,
        expected_marketplace_id=current.marketplace_id, expected_plugin_id=PLUGIN_ID,
        expected_namespace_generation=stamped["namespaceGeneration"],
        expected_install_generation=stamped["generation"], expected_activation_generation=2,
        activation_mode="namespaced", activation_state="active", legacy_disposition="absent",
        legacy_probe={"declared": True, "result": "absent", "checkedAt": "2026-01-01T00:00:00Z"},
        environment={}, os_profile=current.os_profile,
    )
    assert activated["status"] == "ready"
    fresh = WatchInstallation.capture(
        receipt=current.receipt, plugin_root=current.plugin_root, payload_root=payload,
        durable_home=current.durable_home, os_profile=current.os_profile, environment={},
    )
    current.payload_root.rmdir()
    successor = _writer(fresh.state_root)
    try:
        with fresh.transaction(timeout=2, environment={}) as guard:
            result = successor.recover_prepared_state_transition(
                timeout=10, installation=fresh, guard=guard,
            )
        assert result["origin"] == origin
        assert result["binding"] == origin["binding"]
        assert result["controller"]["installation"] == fresh.evidence()
        assert result["controller"]["installation"]["generation"] > current.install_generation
    finally:
        successor.close(timeout=2)


@pytest.mark.parametrize("change", ["origin", "lease", "controller", "sealed", "extra", "version"])
def test_serialized_version_two_refuses_changed_origin_or_controller(
    prepared: tuple[WatchInstallation, WatchInstallation, Document], controller: WatchWriter,
    change: str,
) -> None:
    _, current, _ = prepared
    with current.transaction(timeout=2, environment={}) as guard:
        result = controller.recover_prepared_state_transition(
            timeout=10, installation=current, guard=guard,
        )
    controller.close(timeout=2)
    if change == "origin":
        result["origin"]["lease"]["inode"] += 1
    elif change == "lease":
        result["lease"]["inode"] = True
    elif change == "controller":
        result["controller"]["installation"]["generation"] += 1
    elif change == "sealed":
        result["phase"] = "sealed-legacy"
    elif change == "extra":
        result["unexpected"] = True
    else:
        result["version"] = True
    paths = watch_state_paths(current.state_root)
    write_private(paths.journal, result)
    before = paths.journal.read_bytes()
    successor = _writer(current.state_root)
    try:
        with current.transaction(timeout=2, environment={}) as guard:
            with pytest.raises((ValueError, PermissionError)):
                successor.recover_prepared_state_transition(
                    timeout=10, installation=current, guard=guard,
                )
        assert not successor.writer_held
        assert paths.journal.read_bytes() == before
    finally:
        successor.close(timeout=2)


def test_postpublication_refusal_retains_origin_and_native_custody(
    prepared: tuple[WatchInstallation, WatchInstallation, Document], controller: WatchWriter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import watch_transition_recovery

    _, current, origin = prepared
    persist = watch_transition_recovery.write_private

    def published(*args: Any, **kwargs: Any) -> None:
        persist(*args, **kwargs)

        def refuse(_operation: str) -> None:
            raise PermissionError("controlled postpublication refusal")

        controller.authorize = refuse

    with current.transaction(timeout=2, environment={}) as guard:
        with monkeypatch.context() as race:
            race.setattr(watch_transition_recovery, "write_private", published)
            with pytest.raises(PermissionError, match="postpublication"):
                controller.recover_prepared_state_transition(
                    timeout=10, installation=current, guard=guard,
                )
    result = json.loads(watch_state_paths(current.state_root).journal.read_bytes())
    assert result["origin"] == origin and result["version"] == 2
    assert controller.writer_held and not controller.read_ready


@pytest.mark.parametrize("change", ["native", "admission", "deadline"])
def test_last_source_read_cannot_outlive_current_admission_or_custody(
    prepared: tuple[WatchInstallation, WatchInstallation, Document], controller: WatchWriter,
    monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    from agent_pull_requests import watch_storage, watch_transition_recovery

    _, current, _ = prepared
    paths = watch_state_paths(current.state_root)
    original = paths.journal.read_bytes()
    persist = watch_transition_recovery.write_private
    read = watch_storage._read_subscriptions_state
    remaining = controller._remaining
    publishing = expired = False

    def about_to_publish(*args: Any, **kwargs: Any) -> None:
        nonlocal publishing
        publishing = True
        persist(*args, **kwargs)

    def late_read(*args: Any, **kwargs: Any) -> Any:
        nonlocal expired
        result = read(*args, **kwargs)
        if publishing:
            if change == "native":
                controller._lease.release()
            elif change == "admission":
                policy = current.os_profile / ".copilot-extensions" / "installation-mode.json"
                value = json.loads(policy.read_text(encoding="utf-8"))
                value["installationMode"]["enabled"] = False
                policy.write_text(json.dumps(value), encoding="utf-8")
            else:
                expired = True
        return result

    def deadline(value: float) -> float:
        if expired:
            raise TimeoutError("controlled last-read deadline")
        return remaining(value)

    with current.transaction(timeout=2, environment={}) as guard:
        with monkeypatch.context() as race:
            race.setattr(watch_transition_recovery, "write_private", about_to_publish)
            race.setattr(watch_transition_recovery, "_read_subscriptions_state", late_read)
            race.setattr(controller, "_remaining", deadline)
            with pytest.raises((PermissionError, TimeoutError)):
                controller.recover_prepared_state_transition(
                    timeout=10, installation=current, guard=guard,
                )
    assert paths.journal.read_bytes() == original
    assert not controller.read_ready


def test_original_journal_inode_not_resampled_path_drives_native_acquisition(
    prepared: tuple[WatchInstallation, WatchInstallation, Document], controller: WatchWriter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import watch_transition_recovery

    _, current, _ = prepared
    paths = watch_state_paths(current.state_root)
    original = paths.journal.read_bytes()
    read = watch_transition_recovery._read_prepared
    displaced = current.state_root / "displaced-original.lock"

    def raced(*args: Any, **kwargs: Any) -> tuple[Document, bytes]:
        result = read(*args, **kwargs)
        paths.historical_lock.rename(displaced)
        paths.historical_lock.write_bytes(b"replacement-must-not-be-acquired")
        return result

    with current.transaction(timeout=2, environment={}) as guard:
        with monkeypatch.context() as race:
            race.setattr(watch_transition_recovery, "_read_prepared", raced)
            with pytest.raises(PermissionError):
                controller.recover_prepared_state_transition(
                    timeout=10, installation=current, guard=guard,
                )
    assert not controller.writer_held
    assert paths.historical_lock.read_bytes() == b"replacement-must-not-be-acquired"
    assert paths.journal.read_bytes() == original
    assert displaced.exists() and paths.preparation_pin.exists()


@pytest.mark.parametrize("change", ["authority", "admission"])
def test_initial_source_read_cannot_acquire_after_admission_is_revoked(
    prepared: tuple[WatchInstallation, WatchInstallation, Document], controller: WatchWriter,
    monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    from agent_pull_requests import watch_storage, watch_transition_recovery

    _, current, _ = prepared
    paths = watch_state_paths(current.state_root)
    original, lease_bytes = paths.journal.read_bytes(), paths.historical_lock.read_bytes()
    read = watch_storage._read_subscriptions_state

    def revoked(*args: Any, **kwargs: Any) -> Any:
        result = read(*args, **kwargs)
        if change == "authority":
            def refuse(_operation: str) -> None:
                raise PermissionError("controlled initial-source authority revocation")
            controller.authorize = refuse
        else:
            policy = current.os_profile / ".copilot-extensions" / "installation-mode.json"
            value = json.loads(policy.read_text(encoding="utf-8"))
            value["installationMode"]["enabled"] = False
            policy.write_text(json.dumps(value), encoding="utf-8")
        return result

    with current.transaction(timeout=2, environment={}) as guard:
        with monkeypatch.context() as race:
            race.setattr(watch_transition_recovery, "_read_subscriptions_state", revoked)
            with pytest.raises(PermissionError):
                controller.recover_prepared_state_transition(
                    timeout=10, installation=current, guard=guard,
                )
    assert not controller.writer_held
    assert paths.journal.read_bytes() == original
    assert paths.historical_lock.read_bytes() == lease_bytes


@pytest.mark.parametrize("change", ["journal", "topology"])
@pytest.mark.parametrize("boundary", ["source", "authority"])
def test_final_read_or_admission_cannot_hide_changed_journal_or_topology(
    prepared: tuple[WatchInstallation, WatchInstallation, Document], controller: WatchWriter,
    monkeypatch: pytest.MonkeyPatch, change: str, boundary: str,
) -> None:
    from agent_pull_requests import watch_storage, watch_transition_recovery

    _, current, _ = prepared
    paths = watch_state_paths(current.state_root)
    original = paths.journal.read_bytes()
    persist = watch_transition_recovery.write_private
    read = watch_storage._read_subscriptions_state
    publishing = changed = False

    def mutate() -> None:
        nonlocal changed
        if changed:
            return
        changed = True
        if change == "journal":
            paths.journal.write_bytes(original + b"\n")
        else:
            paths.staged_barrier.mkdir()

    def about_to_publish(*args: Any, **kwargs: Any) -> None:
        nonlocal publishing
        publishing = True
        persist(*args, **kwargs)

    def source(*args: Any, **kwargs: Any) -> Any:
        result = read(*args, **kwargs)
        if publishing and boundary == "source":
            mutate()
        return result

    def authorize(_operation: str) -> None:
        if publishing and boundary == "authority":
            mutate()

    with current.transaction(timeout=2, environment={}) as guard:
        with monkeypatch.context() as race:
            race.setattr(controller, "authorize", authorize)
            race.setattr(watch_transition_recovery, "write_private", about_to_publish)
            race.setattr(watch_transition_recovery, "_read_subscriptions_state", source)
            with pytest.raises(PermissionError):
                controller.recover_prepared_state_transition(
                    timeout=10, installation=current, guard=guard,
                )
    assert changed
    assert paths.journal.read_bytes() == original + (b"\n" if change == "journal" else b"")
    assert controller.writer_held and not controller.read_ready
    assert paths.staged_barrier.exists() is (change == "topology")


@pytest.mark.parametrize("oversized", [False, True])
def test_serialized_receiver_size_is_checked_before_acquisition_or_publication(
    prepared: tuple[WatchInstallation, WatchInstallation, Document], controller: WatchWriter,
    oversized: bool,
) -> None:
    from agent_pull_requests.watch_storage import _MAX_METADATA_BYTES

    _, current, origin = prepared
    origin["binding"]["installation"]["payloadRoot"] = current.payload_root.anchor
    candidate = {
        **origin, "version": 2, "origin": origin,
        "controller": {
            "kind": "installation", "stateRoot": str(current.state_root),
            "installation": current.evidence(),
        },
    }
    overhead = len(json.dumps(candidate).encode())
    padding = (_MAX_METADATA_BYTES - overhead) // 2 + int(oversized)
    origin["binding"]["installation"]["payloadRoot"] += "x" * padding
    assert (len(json.dumps(candidate).encode()) > _MAX_METADATA_BYTES) is oversized
    paths = watch_state_paths(current.state_root)
    write_private(paths.journal, origin)
    original = paths.journal.read_bytes()
    assert len(original) < _MAX_METADATA_BYTES
    with current.transaction(timeout=2, environment={}) as guard:
        if oversized:
            with pytest.raises(ValueError, match="bounded size"):
                controller.recover_prepared_state_transition(
                    timeout=10, installation=current, guard=guard,
                )
            assert paths.journal.read_bytes() == original
            assert not controller.writer_held
        else:
            result = controller.recover_prepared_state_transition(
                timeout=10, installation=current, guard=guard,
            )
            assert result["origin"] == origin
            assert len(paths.journal.read_bytes()) <= _MAX_METADATA_BYTES
            assert controller.writer_held


@pytest.mark.parametrize("reason", ["active", "unauthorized"])
def test_initial_refusal_does_not_fence_or_change_existing_owner(
    tmp_path: Path, reason: str,
) -> None:
    from agent_pull_requests.watch_contract import PRSnapshot

    owner = WatchWriter(root=tmp_path, authorize=lambda _: None, fetch=lambda *_: PRSnapshot())
    try:
        owner.adopt(timeout=2)
        owner.activate(timeout=2)
        if reason == "unauthorized":
            def refuse(_operation: str) -> None:
                raise PermissionError("controlled unauthorized recovery")
            owner.authorize = refuse
        with pytest.raises(PermissionError):
            owner.recover_prepared_state_transition(timeout=2)
        assert owner.writer_held and owner.read_ready
        assert owner.daemon.effects_enabled
        assert owner._preparation_guard is None and not owner._strict_custody
        assert not watch_state_paths(tmp_path).journal.exists()
    finally:
        owner.close(timeout=2)


def test_fresh_controller_ignores_only_stale_launch_payload_environment(
    prepared: tuple[WatchInstallation, WatchInstallation, Document], controller: WatchWriter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old, current, origin = prepared
    monkeypatch.setenv("COPILOT_PLUGIN_ROOT", str(old.payload_root))
    monkeypatch.setenv("WSL_DISTRO_NAME", "Observed Fixture Guest")
    require_current = WatchInstallation.require_current

    def preserved(self: WatchInstallation, **kwargs: Any) -> Document:
        environment = kwargs["environment"]
        assert environment["WSL_DISTRO_NAME"] == "Observed Fixture Guest"
        assert "COPILOT_PLUGIN_ROOT" not in environment
        return require_current(self, **kwargs)

    with current.transaction(timeout=2, environment={}) as guard:
        with monkeypatch.context() as checks:
            checks.setattr(WatchInstallation, "require_current", preserved)
            result = controller.recover_prepared_state_transition(
                timeout=10, installation=current, guard=guard,
            )
    assert result["origin"] == origin
    assert result["controller"]["installation"] == current.evidence()
    assert controller.writer_held


@pytest.mark.parametrize("registered", [False, True])
def test_current_controller_preserves_guest_observation_and_conflict_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: bool,
) -> None:
    from agent_pull_requests import _installation_context as primitive
    from agent_pull_requests.watch_transition_recovery import _controller_environment

    monkeypatch.setattr(primitive, "_is_wsl_guest", lambda: True)
    monkeypatch.setattr(
        primitive, "_read_guest_environment",
        lambda _profile: {"distribution": "Example Distro"} if registered else None,
    )
    monkeypatch.setenv("WSL_DISTRO_NAME", "Other Distro" if registered else "Example Distro")
    monkeypatch.setenv("COPILOT_PLUGIN_ROOT", str(tmp_path / "stale-launch-payload"))
    monkeypatch.setenv("AGENT_FIXTURE_CURRENT_SCOPE", "retain-current-observation")
    environment = _controller_environment()
    assert "COPILOT_PLUGIN_ROOT" not in environment
    assert environment["AGENT_FIXTURE_CURRENT_SCOPE"] == "retain-current-observation"
    resolve = cast(Callable[..., str | None], getattr(primitive, "_resolve_wsl_distribution"))
    if registered:
        with pytest.raises(ValueError, match="guest-identity-conflict"):
            resolve(tmp_path, environment=environment, explicit=None)
    else:
        assert resolve(tmp_path, environment=environment, explicit=None) == "Example Distro"
