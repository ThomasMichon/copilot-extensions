"""Original service and connector outcomes fence application reclamation."""

from __future__ import annotations

from contextlib import contextmanager
import importlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Callable, Iterator, cast
import time

import pytest


INSTANCE = "a" * 32


@pytest.fixture
def removal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    directory = Path(__file__).resolve().parents[3] / "libs" / "installer-engine"
    monkeypatch.syspath_prepend(str(directory))
    spec = importlib.util.spec_from_file_location(
        "_driver_removal_artifact_fixture", directory / "runtime_application_artifacts.py",
    )
    assert spec is not None and spec.loader is not None
    artifacts = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(artifacts)
    root = tmp_path / "application"
    slot = root / "versions" / "1.0.0"
    slot.mkdir(parents=True)
    (slot / ".runtime-bootstrap").mkdir()
    endpoint = slot / ".runtime-bootstrap" / f"driver-service-{INSTANCE}.json"
    endpoint.write_bytes(b'{"private":"not copied into the journal"}')
    (root / "current-version").write_text(slot.name, encoding="utf-8")
    registry = tmp_path / "registry"
    registry.mkdir()
    record_path = registry / f"{INSTANCE}.instance"
    record_path.write_bytes(b"canonical original registration")
    packet = tmp_path / "packet"
    packet.mkdir()
    packet_record = packet / "removal-packet.json"
    packet_record.write_text(json.dumps({
        "pending_artifacts": {
            endpoint.relative_to(slot).as_posix(): artifacts._state(
                endpoint, time.monotonic() + 10,
            ),
        },
    }), encoding="utf-8")
    plan = SimpleNamespace(
        root=root, slot=slot, component="agent-remote-driver",
        payload_hash="b" * 64, profile_lock_id="sha256:" + "c" * 64,
        command_directory=tmp_path / "global-bin",
    )
    record = {
        "instance_id": INSTANCE, "service": "agent-remote-driver-service",
        "runtime_root": str(slot), "pid": "123", "birth": "canonical-native-birth",
        "protected": "0", "process_identity_state": "matching",
        "observation_driver_service_state_path": str(endpoint),
    }
    return SimpleNamespace(
        root=root, slot=slot, endpoint=endpoint, registry=registry, record_path=record_path,
        packet=packet, plan=plan, record=record, artifacts=artifacts, calls=[], depth=[0],
        journal=tmp_path / "transaction", home=tmp_path / "copilot",
        activation=[0], connector_changed=False, host_record=None,
    )


def _wire(removal: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = importlib.import_module("agent_remote_driver_app.application_removal")
    lifecycle = module.lifecycle
    provider = SimpleNamespace(
        read_application_removal=lambda packet, **_: removal.plan,
        _removal_unchanged=lambda plan: None,
        _artifacts=lambda _: removal.artifacts,
        _resources=lambda _: (SimpleNamespace(
            slot_python=lambda root, generation: root / "versions" / generation / ".venv"
            / ("Scripts" if module.os.name == "nt" else "bin")
            / ("python.exe" if module.os.name == "nt" else "python"),
        ), None),
    )

    @contextmanager
    def lock(path: Path) -> Iterator[None]:
        service = path.name == "driver-service-start.lock"
        if service:
            assert removal.depth[0] == 0
            removal.depth[0] += 1
        try:
            yield
        finally:
            if service:
                removal.depth[0] -= 1

    def retire(binding: Any, instance_id: str, *, deadline: float) -> None:
        assert removal.depth == [1] and instance_id == INSTANCE
        assert (removal.journal / "service.intent.json").is_file()
        intent = json.loads((removal.journal / "service.intent.json").read_bytes())
        assert intent["original"]["birth"] == removal.record["birth"]
        assert intent["original"]["pid"] == 123
        assert intent["original"]["registry_root"] == str(removal.registry)
        assert intent["original"]["endpoint_state"] == removal.artifacts._state(
            removal.endpoint, deadline,
        )
        removal.calls.append("service")
        removal.record_path.unlink()
        removal.endpoint.unlink()

    @contextmanager
    def withdraw(binding: Any, *, withdraw: bool = True, **_: Any) -> Iterator[dict[str, bool]]:
        assert removal.depth == [1]
        assert (removal.journal / "service.settled.json").is_file()
        assert (removal.journal / "connector.intent.json").is_file()
        if withdraw:
            assert binding.python.parent.parent == binding.slot / ".venv"
            removal.calls.append("connector")
        if removal.connector_changed:
            raise RuntimeError("Driver connector acquired another owner")
        removal.activation[0] += 1
        try:
            yield {"withdrawn": True, "assets_retained": True}
        finally:
            removal.activation[0] -= 1

    def publications(*_: Any, **__: Any) -> None:
        assert removal.depth == [1]
        assert removal.activation == [1]
        assert (removal.journal / "connector.settled.json").is_file()
        removal.calls.append("publications")

    def reclaim(*_: Any, validate: Callable[[], None], **__: Any) -> None:
        assert removal.depth == [1]
        assert removal.activation == [1]
        validate()
        removal.calls.append("reclaim")

    monkeypatch.setattr(module, "OperationLock", lock)
    monkeypatch.setattr(lifecycle, "load_provider", lambda _: provider)
    monkeypatch.setattr(module, "default_registry_root", lambda: removal.registry)
    def observe(*_: Any, service: str | None = None) -> list[dict[str, str]]:
        records = [removal.record] if removal.record_path.exists() else []
        if removal.host_record is not None:
            records.append(removal.host_record)
        return [row for row in records if service is None or row["service"] == service]

    monkeypatch.setattr(module, "observe_instances", observe)
    monkeypatch.setattr(lifecycle, "_settle_service_removal_locked", retire)
    monkeypatch.setattr(module, "PinnedProcess", lambda *_: (_ for _ in ()).throw(
        ProcessLookupError(),
    ))
    monkeypatch.setattr(module.connector, "withdrawal_custody", withdraw)
    provider.withdraw_application_publications = publications
    provider.reclaim_application_removal = reclaim
    removal.provider = provider
    removal.module = module
    return module


def _remove(removal: SimpleNamespace) -> dict[str, str]:
    return cast(dict[str, str], removal.module.remove(
        removal.packet, removal.journal, INSTANCE, deadline=time.monotonic() + 10,
        home=removal.home, executable="fixture-copilot",
    ))


def test_original_receipts_precede_effects_under_one_start_lock(
    removal: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(removal, monkeypatch)
    result = _remove(removal)
    assert result == {"state": "removed", "instance_id": INSTANCE, "generation": "1.0.0"}
    assert removal.calls == ["service", "connector", "publications", "reclaim"]
    assert removal.depth == [0]
    assert b"private" not in b"".join(path.read_bytes() for path in removal.journal.glob("*.json"))


def test_changed_original_selection_refuses_before_native_service_intent(
    removal: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(removal, monkeypatch)

    def changed(_plan: Any) -> None:
        raise RuntimeError("original application publication changed")

    removal.provider._removal_unchanged = changed
    with pytest.raises(RuntimeError, match="publication changed"):
        _remove(removal)
    assert removal.calls == []
    assert not removal.journal.exists()


def test_public_callback_prepares_exact_pending_service_then_uses_external_transaction(
    removal: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _wire(removal, monkeypatch)
    workspace = removal.root.parent / "public-removal"
    workspace.mkdir()
    observed = []
    removal.provider.plan_application_removal = lambda *a, **kw: removal.plan

    def prepare(plan: Any, packet: Path, **options: Any) -> None:
        assert plan is removal.plan and packet == workspace / "packet"
        assert options["pending_artifacts"] == (removal.endpoint,)
        observed.append("prepare")
        packet.mkdir()

    def remove(packet: Path, journal: Path, instance_id: str, **_kw: Any) -> dict[str, str]:
        assert (packet, journal, instance_id) == (
            workspace / "packet", workspace / "journal", INSTANCE,
        )
        observed.append("remove")
        return {"state": "removed"}

    removal.provider.prepare_application_removal = prepare
    monkeypatch.setattr(module, "remove", remove)
    assert module.main(
        provider=removal.provider, slot=removal.slot, workspace=workspace,
        arguments=["--instance-id", INSTANCE], deadline=time.monotonic() + 10,
    ) == {"state": "removed"}
    assert observed == ["prepare", "remove"]
    observed.clear()
    assert module.main(
        provider=removal.provider, slot=removal.slot, workspace=workspace,
        arguments=["--instance-id", INSTANCE], deadline=time.monotonic() + 10,
    ) == {"state": "removed"}
    assert observed == ["remove"]


@pytest.mark.parametrize("instance_id", ["", "wrong", "b" * 31, "A" * 32])
def test_public_callback_requires_original_exact_instance_before_packet_preparation(
    removal: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, instance_id: str,
) -> None:
    module = _wire(removal, monkeypatch)
    with pytest.raises(ValueError, match="instance"):
        module.main(
            provider=removal.provider, slot=removal.slot, workspace=removal.root.parent,
            arguments=["--instance-id", instance_id], deadline=time.monotonic() + 10,
        )
    assert removal.calls == []


@pytest.mark.parametrize("damage", ["extra", "missing", "pid", "endpoint", "registry"])
def test_resume_refuses_malformed_original_native_snapshot_before_any_effect(
    removal: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, damage: str,
) -> None:
    _wire(removal, monkeypatch)
    assert _remove(removal)["state"] == "removed"
    transaction = removal.journal / "transaction.json"
    record = json.loads(transaction.read_bytes())
    if damage == "extra":
        record["original"]["unrecognized"] = True
    elif damage == "missing":
        del record["original"]["registration_state"]
    elif damage == "pid":
        record["original"]["pid"] = True
    else:
        record["original"][damage if damage == "endpoint" else "registry_root"] = "../foreign"
    transaction.write_text(json.dumps(record), encoding="utf-8")
    for phase in ("service", "connector"):
        for state in ("intent", "settled"):
            (removal.journal / f"{phase}.{state}.json").write_text(
                json.dumps(record), encoding="utf-8",
            )
    before = removal.calls[:]
    with pytest.raises(ValueError, match="snapshot"):
        _remove(removal)
    assert removal.calls == before


@pytest.mark.parametrize("phase", ["service", "connector"])
def test_unknown_native_intent_never_replays(
    removal: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, phase: str,
) -> None:
    module = _wire(removal, monkeypatch)

    def unknown(*_: Any, **__: Any) -> None:
        removal.calls.append(phase + "-unknown")
        raise OSError("native result lost")

    if phase == "service":
        monkeypatch.setattr(module.lifecycle, "_settle_service_removal_locked", unknown)
    else:
        monkeypatch.setattr(module.connector, "withdrawal_custody", unknown)
    with pytest.raises(module.RemovalUncertain, match=phase):
        _remove(removal)
    before = removal.calls[:]
    with pytest.raises(module.RemovalUncertain, match=phase):
        _remove(removal)
    assert removal.calls == before and removal.depth == [0]
    assert "publications" not in removal.calls


@pytest.mark.parametrize("phase", ["connector", "publications", "reclaim"])
def test_settled_service_resume_uses_original_evidence_without_fresh_retirement(
    removal: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, phase: str,
) -> None:
    module = _wire(removal, monkeypatch)
    target, attribute = (
        (module.connector, "withdrawal_custody") if phase == "connector" else
        (removal.provider, "withdraw_application_publications") if phase == "publications" else
        (removal.provider, "reclaim_application_removal")
    )
    original = getattr(target, attribute)

    def fail(*_: Any, **__: Any) -> None:
        raise OSError("interrupted phase")

    monkeypatch.setattr(target, attribute, fail)
    expected = module.RemovalUncertain if phase == "connector" else OSError
    with pytest.raises(expected):
        _remove(removal)
    assert not removal.record_path.exists()
    if phase == "connector":
        return
    monkeypatch.setattr(target, attribute, original)
    assert _remove(removal)["state"] == "removed"
    assert removal.calls.count("service") == removal.calls.count("connector") == 1


def test_independent_host_refuses_reclamation_and_retry_keeps_native_phases_settled(
    removal: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(removal, monkeypatch)
    original = removal.provider.reclaim_application_removal
    monkeypatch.setattr(removal.provider, "reclaim_application_removal", lambda *_, **__: (
        (_ for _ in ()).throw(RuntimeError("runtime retention lease is held"))
    ))
    with pytest.raises(RuntimeError, match="retention"):
        _remove(removal)
    before = removal.calls[:]
    monkeypatch.setattr(removal.provider, "reclaim_application_removal", original)
    assert _remove(removal)["state"] == "removed"
    assert removal.calls == before + ["publications", "reclaim"]


def test_settled_connector_reactivation_refuses_before_filesystem_retry(
    removal: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(removal, monkeypatch)
    monkeypatch.setattr(removal.provider, "reclaim_application_removal", lambda *_, **__: (
        (_ for _ in ()).throw(RuntimeError("runtime retention lease is held"))
    ))
    with pytest.raises(RuntimeError, match="retention"):
        _remove(removal)
    removal.connector_changed = True
    before = removal.calls[:]
    with pytest.raises(RuntimeError, match="connector"):
        _remove(removal)
    assert removal.calls == before and removal.activation == [0]


@pytest.mark.parametrize("finished", [False, True])
def test_independent_host_artifact_owner_must_settle_before_reclamation(
    removal: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, finished: bool,
) -> None:
    _wire(removal, monkeypatch)
    removal.host_record = {
        "instance_id": "d" * 32, "service": "session-host",
        "runtime_root": str(removal.slot / "core"),
        "state": "finished" if finished else "running",
        "process_identity_state": "dead" if finished else "matching",
    }
    with pytest.raises(RuntimeError, match="independent.*artifact"):
        _remove(removal)
    assert removal.calls == ["service", "connector"]
    assert removal.depth == removal.activation == [0]
    removal.host_record = None
    assert _remove(removal)["state"] == "removed"
    assert removal.calls == ["service", "connector", "publications", "reclaim"]


@pytest.mark.parametrize("changed", ["registration", "endpoint"])
def test_native_retirement_revalidates_original_physical_intent(
    removal: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, changed: str,
) -> None:
    module = _wire(removal, monkeypatch)
    original = removal.artifacts._write_once
    target = removal.record_path if changed == "registration" else removal.endpoint

    def replace_after_intent(path: Path, record: dict[str, Any], deadline: float) -> None:
        original(path, record, deadline)
        if path.name == "service.intent.json":
            content = target.read_bytes()
            target.rename(target.with_name(target.name + ".original"))
            target.write_bytes(content)

    monkeypatch.setattr(removal.artifacts, "_write_once", replace_after_intent)
    with pytest.raises(module.RemovalUncertain, match="service"):
        _remove(removal)
    assert removal.calls == [] and removal.depth == [0]


def test_reference_appearing_after_withdrawal_is_rechecked_before_reclamation(
    removal: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(removal, monkeypatch)
    original = removal.provider.withdraw_application_publications

    def late_reference(*args: Any, **kwargs: Any) -> None:
        original(*args, **kwargs)
        removal.host_record = {
            "instance_id": "d" * 32, "service": "session-host", "state": "finished",
            "runtime_root": str(removal.slot / "core"), "process_identity_state": "dead",
        }

    monkeypatch.setattr(removal.provider, "withdraw_application_publications", late_reference)
    with pytest.raises(RuntimeError, match="independent.*artifact"):
        _remove(removal)
    assert removal.calls == ["service", "connector", "publications"]
    assert removal.depth == removal.activation == [0]


@pytest.mark.parametrize("changed", ["endpoint", "registration", "successor", "live", "registry"])
def test_known_service_settlement_does_not_authorize_changed_or_live_owners(
    removal: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, changed: str,
) -> None:
    module = _wire(removal, monkeypatch)
    monkeypatch.setattr(removal.provider, "withdraw_application_publications", lambda *_, **__: (
        (_ for _ in ()).throw(OSError("pause before publications"))
    ))
    with pytest.raises(OSError):
        _remove(removal)
    if changed == "endpoint":
        removal.endpoint.write_bytes(b"replacement endpoint")
    elif changed == "registration":
        removal.record_path.write_bytes(b"replacement registration")
    elif changed == "successor":
        successor = dict(removal.record, instance_id="d" * 32)
        monkeypatch.setattr(module, "observe_instances", lambda *_, **__: [successor])
    elif changed == "registry":
        other = removal.registry.parent / "other-registry"
        other.mkdir()
        monkeypatch.setattr(module, "default_registry_root", lambda: other)
    else:
        @contextmanager
        def live(*_: Any) -> Iterator[Any]:
            yield SimpleNamespace(exited=lambda: False)
        monkeypatch.setattr(module, "PinnedProcess", live)
    before = removal.calls[:]
    with pytest.raises((RuntimeError, ValueError), match="original|service|registry"):
        _remove(removal)
    assert removal.calls == before


@pytest.mark.parametrize("changed", ["packet", "instance", "home", "pending"])
def test_original_transaction_or_pending_endpoint_mismatch_refuses_effects(
    removal: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, changed: str,
) -> None:
    module = _wire(removal, monkeypatch)
    if changed == "pending":
        (removal.packet / "removal-packet.json").write_text(
            '{"pending_artifacts":{}}', encoding="utf-8",
        )
    else:
        _remove(removal)
        if changed == "packet":
            (removal.packet / "removal-packet.json").write_bytes(b"{}")
        elif changed == "instance":
            before = removal.calls[:]
            with pytest.raises((ValueError, RuntimeError), match="original|transaction"):
                module.remove(
                    removal.packet, removal.journal, "e" * 32,
                    deadline=time.monotonic() + 10, home=removal.home,
                    executable="fixture-copilot",
                )
            assert removal.calls == before
            return
        else:
            removal.home = removal.home.parent / "different-home"
    before = removal.calls[:]
    with pytest.raises((ValueError, RuntimeError), match="original|transaction|pending"):
        _remove(removal)
    assert removal.calls == before
