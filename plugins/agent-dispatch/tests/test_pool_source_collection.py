"""The reconciliation owner's complete-set boundary, not live placement."""

from __future__ import annotations

import copy
import json
import time
from concurrent.futures import Future
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
from typing import Any

import pytest
from dropin_registry import ScanAuthority

from agent_dispatch.companion import CompanionIndeterminate
from agent_dispatch.pool_instances import PoolInstanceDefinition
from agent_dispatch.pool_source_snapshot import (
    DeclarationRead, SourceObservation, collect_pool_sources, read_supervisor_declarations,
)
from agent_dispatch.registrar import load_declaration
from agent_dispatch.registrar_reconcile import declaration_to_registration
from agent_dispatch.supervisor_daemon import SupervisorDaemon
from agent_dispatch.supervisor_daemon_registration_publish import BoundedDeclarationPublisher
from tests.test_managed_companion import Executor
from tests.test_supervisor_daemon import FakeClient, FakeCompanionController, FakeLauncher


def _declaration(name: str = "worker", **fields: Any) -> Any:
    return load_declaration({
        "name": name, "owner": "repo:example", "repos": "example",
        "labels": [name], "priority": 10, "concurrency": 2,
        "filters": {"permit": {"role": ["review"]}, "reject": {"worktree": ["excluded"]}},
        **fields,
    })


def _registration(name: str = "worker", **fields: Any) -> dict:
    return declaration_to_registration(_declaration(name, **fields), machine="host-a")


def _daemon(rows: list[dict] | None = None, **kwargs: Any) -> SupervisorDaemon:
    daemon = SupervisorDaemon(
        FakeClient(rows or []), "host-a", launcher=FakeLauncher(),
        companion_controller=FakeCompanionController(), overrides_source=lambda: {},
        **kwargs,
    )
    daemon._publication_executor = Executor()
    return daemon


def test_complete_source_uses_actual_precedence_order_and_full_filters() -> None:
    declaration = _declaration()
    direct = _registration()
    direct["id"] = "legacy"
    direct.pop("pool_definition")
    independent = _registration("independent", priority=20)
    daemon = _daemon(
        [direct, independent],
        declared_source=lambda: DeclarationRead((declaration,), True),
    )
    try:
        desired = daemon._desired()
        snapshot = daemon.pool_source_snapshot()
        assert snapshot.complete
        assert snapshot.attempt == 1
        assert daemon._deduplicated == ["legacy"]
        assert set(desired) == {"declared:repo:example:worker", independent["id"]}
        assert [definition.registration_id for definition in snapshot.definitions] == [
            independent["id"], "declared:repo:example:worker",
        ]
        definition = snapshot.definitions[1]
        assert definition.filters.permit["repo"] == frozenset({"example"})
        assert definition.filters.permit["task-type"] == frozenset({"worker"})
        assert definition.filters.permit["role"] == frozenset({"review"})
        assert definition.filters.reject["worktree"] == frozenset({"excluded"})
        direct["spec"]["labels"] = ["later-change"]
        assert definition.filters.permit["task-type"] == frozenset({"worker"})
        with pytest.raises(FrozenInstanceError):
            snapshot.attempt = 100
        with pytest.raises(TypeError):
            definition.filters.permit["role"] = frozenset()
    finally:
        daemon.shutdown()


@pytest.mark.parametrize("veto", ["legacy", "declared:repo:example:worker", "logical:repo:example:worker"])
def test_override_veto_survives_equivalent_source_replacement(veto: str) -> None:
    declaration = _declaration()
    direct = _registration()
    direct["id"] = "legacy"
    direct.pop("pool_definition")
    daemon = _daemon([direct], declared_source=lambda: DeclarationRead((declaration,), True))
    daemon.overrides_source = lambda: {veto: {"disabled": True}}
    try:
        assert daemon._desired() == {}
        assert daemon.pool_source_snapshot().complete
        assert daemon.pool_source_snapshot().definitions == ()
    finally:
        daemon.shutdown()


def test_conflicting_specs_keep_both_pools_and_direct_pause_is_excluded() -> None:
    declaration = _declaration()
    conflicting = _registration("worker", labels=["different"])
    conflicting["id"] = "direct"
    conflicting["pool_definition"]["registration_id"] = "direct"
    paused = _registration("paused")
    paused["status"] = "paused"
    daemon = _daemon([conflicting, paused], declared_source=lambda: DeclarationRead((declaration,), True))
    try:
        assert set(daemon._desired()) == {"direct", "declared:repo:example:worker"}
        assert daemon._conflicts
        assert daemon.pool_source_snapshot().complete
        assert len(daemon.pool_source_snapshot().definitions) == 2
    finally:
        daemon.shutdown()


@pytest.mark.parametrize("invalid", ["missing", "foreign", "capacity", "partial-filters"])
def test_one_invalid_effective_worker_refuses_entire_collection_without_changing_desired(invalid: str) -> None:
    row = _registration("invalid")
    if invalid == "missing":
        row.pop("pool_definition")
    elif invalid == "foreign":
        row["pool_definition"]["machine"] = "host-b"
    elif invalid == "capacity":
        row["pool_definition"]["max_concurrent"] = 3
    else:
        row["pool_definition"]["filters"].pop("reject")
    daemon = _daemon([_registration("valid"), row])
    try:
        assert len(daemon._desired()) == 2
        snapshot = daemon.pool_source_snapshot()
        assert not snapshot.complete
        assert snapshot.definitions == ()
        assert "effective-worker-definition-invalid" in snapshot.reasons
    finally:
        daemon.shutdown()


def test_nonworker_missing_definition_does_not_block_and_duplicate_ids_do() -> None:
    row = {"id": "evaluator", "kind": "evaluator", "spec": {}, "machine": "host-a", "env": "default"}
    daemon = _daemon([row])
    try:
        assert daemon._desired() == {"evaluator": row}
        assert daemon.pool_source_snapshot().complete
        duplicate = _registration()
        daemon.client.set_regs([duplicate, copy.deepcopy(duplicate)])
        daemon._desired()
        assert daemon.pool_source_snapshot().definitions == ()
        assert "duplicate-source-registration" in daemon.pool_source_snapshot().reasons
    finally:
        daemon.shutdown()


@pytest.mark.parametrize("confirmed", [False, True])
def test_declaration_failure_retains_legacy_custody_but_never_complete(confirmed: bool) -> None:
    def unavailable() -> list:
        raise RuntimeError("source-private-details")

    daemon = _daemon(declared_source=unavailable)
    if confirmed:
        daemon._last_declared = [_registration()]
    try:
        if confirmed:
            assert daemon._desired()
        else:
            with pytest.raises(CompanionIndeterminate):
                daemon._desired()
        snapshot = daemon.pool_source_snapshot()
        assert not snapshot.complete and not snapshot.definitions
        assert any(item.source == "declarations" and item.state in {"cached", "unavailable"}
                   for item in snapshot.observations)
    finally:
        daemon.shutdown()


@pytest.mark.parametrize("source", ["declarations", "registrations"])
def test_cached_then_delayed_result_needs_a_new_source_read(source: str, monkeypatch: Any) -> None:
    declaration = _declaration()
    daemon = _daemon(
        [_registration("direct")], declared_source=lambda: DeclarationRead((declaration,), True),
    )
    try:
        daemon._desired()
        original = daemon.pool_source_snapshot()
        assert original.complete
        future: Future = Future()
        daemon._discovery_futures[source] = future
        daemon._probe_inflight.add(future)
        daemon._reconcile_deadline = time.monotonic()
        assert daemon._desired()
        assert not daemon.pool_source_snapshot().complete
        if source == "declarations":
            future.set_result(([_registration()], True, ""))
        else:
            rows = daemon.client.list_registrations(machine="host-a", env="default", include_paused=False)
            future.set_result((0, rows, copy.deepcopy(rows)))
        daemon._reconcile_deadline = time.monotonic() + 1
        daemon._desired()
        assert any(item.source == source and item.state == "delayed"
                   for item in daemon.pool_source_snapshot().observations)
        assert not daemon.pool_source_snapshot().complete
        daemon._reconcile_deadline = None
        daemon._desired()
        assert daemon.pool_source_snapshot().complete
        assert original.attempt == 1
        assert daemon.pool_source_snapshot().attempt == 4
    finally:
        daemon.shutdown()


def test_unconfirmed_plain_source_and_partial_report_cannot_renew_collection() -> None:
    declaration = _declaration()
    daemon = _daemon(declared_source=lambda: [declaration])
    try:
        assert daemon._desired()
        assert "declarations:source-completeness-unconfirmed" in daemon.pool_source_snapshot().reasons
        daemon.declared_source = lambda: DeclarationRead((declaration,), False, "retained-source")
        assert daemon._desired()
        assert "declarations:retained-source" in daemon.pool_source_snapshot().reasons
    finally:
        daemon.shutdown()


@pytest.mark.parametrize("state", ["pending", "unknown", "withdrawal", "future"])
def test_unsettled_declaration_metadata_refuses_complete(state: str) -> None:
    publisher = BoundedDeclarationPublisher()
    if state == "withdrawal":
        publisher.withdrawn_ids.add("withdrawn")
    elif state == "future":
        publisher.future = Future()
    else:
        publisher.records["pending"] = {"state": state, "target": None}
    daemon = _daemon()
    if state == "withdrawal":
        row = _registration()
        row["id"] = "withdrawn"
        row["pool_definition"]["registration_id"] = "withdrawn"
        daemon.client.set_regs([row])
    daemon._declaration_publisher = publisher
    try:
        daemon._desired()
        assert "declaration-publication-unconfirmed" in daemon.pool_source_snapshot().reasons
        assert not daemon.pool_source_snapshot().definitions
    finally:
        daemon.shutdown()


@pytest.mark.parametrize("payload", ["not json", "[]", '{"valid":{"disabled":true},"broken":1}'])
def test_strict_override_failure_preserves_valid_legacy_veto_and_blocks_collection(
    payload: str, tmp_path: Any, monkeypatch: Any,
) -> None:
    from agent_dispatch import config

    path = tmp_path / "overrides.json"
    path.write_text(payload, encoding="utf-8")
    monkeypatch.setattr(config, "overrides_path", lambda: path)
    valid = _registration()
    valid["id"] = "valid"
    valid["pool_definition"]["registration_id"] = "valid"
    daemon = _daemon([valid])
    daemon.overrides_source = None
    try:
        desired = daemon._desired()
        assert bool(desired) == ("valid" not in payload)
        assert not daemon.pool_source_snapshot().complete
        assert "overrides:unavailable" in daemon.pool_source_snapshot().reasons
    finally:
        daemon.shutdown()


def test_missing_override_file_is_confirmed_absence(tmp_path: Any, monkeypatch: Any) -> None:
    from agent_dispatch import config

    monkeypatch.setattr(config, "overrides_path", lambda: tmp_path / "missing.json")
    daemon = _daemon([_registration()])
    daemon.overrides_source = None
    try:
        assert daemon._desired()
        assert daemon.pool_source_snapshot().complete
    finally:
        daemon.shutdown()


def test_override_read_permission_failure_does_not_manufacture_empty_complete(
    monkeypatch: Any, caplog: Any,
) -> None:
    def unavailable() -> dict:
        raise PermissionError("private-path")

    daemon = _daemon([_registration()])
    daemon.overrides_source = unavailable
    try:
        assert daemon._desired()
        assert not daemon.pool_source_snapshot().complete
        assert "private-path" not in caplog.text
    finally:
        daemon.shutdown()


@pytest.mark.parametrize("trusted,plugins,findings,complete", [
    (ScanAuthority.COMPLETE, ScanAuthority.COMPLETE, (), True),
    (ScanAuthority.ABSENT, ScanAuthority.ABSENT, (), True),
    (ScanAuthority.INDETERMINATE, ScanAuthority.COMPLETE, (), False),
    (ScanAuthority.COMPLETE, ScanAuthority.INDETERMINATE, (), False),
    (ScanAuthority.COMPLETE, ScanAuthority.COMPLETE, ("retained-or-rejected-entry",), False),
])
def test_registrar_adapter_uses_same_refresh_report(
    trusted: ScanAuthority, plugins: ScanAuthority, findings: tuple, complete: bool,
) -> None:
    declarations = (_declaration(),)
    calls = []
    report = SimpleNamespace(
        trusted_authority=trusted,
        combined=SimpleNamespace(
            declarations=declarations,
            plugins=SimpleNamespace(snapshot=SimpleNamespace(authority=plugins), findings=findings),
        ),
    )
    def refresh() -> Any:
        calls.append("refresh")
        return report

    result = read_supervisor_declarations(SimpleNamespace(refresh=refresh))
    assert result.complete == complete
    assert result.declarations is declarations
    assert calls == ["refresh"]
    assert bool(result.reason) != complete


def test_legacy_adapter_keeps_declarations_but_requires_source_qualification() -> None:
    declaration = _declaration()
    result = read_supervisor_declarations(
        SimpleNamespace(discover_with_legacy=lambda: [declaration]), legacy=True,
    )
    assert list(result) == [declaration]
    assert not result.complete
    assert result.reason == "legacy-source-unconfirmed"


def test_snapshot_boundary_requires_every_source_and_explicit_scope() -> None:
    observations = tuple(SourceObservation(key, "fresh") for key in ("declarations", "registrations", "overrides"))
    snapshot = collect_pool_sources(
        {}, machine="host-a", env="default", attempt=1, observed_at=10, observations=observations,
    )
    assert snapshot.complete and not snapshot.definitions
    assert not replace(snapshot, reasons=("pending",)).complete
    for inputs, machine in (((), "host-a"), (observations, None), (observations[:-1], "host-a")):
        assert not collect_pool_sources(
            {}, machine=machine, env="default", attempt=2, observed_at=20, observations=inputs,
        ).complete


def test_collection_precedes_companion_mutation_and_inspection_does_no_io(monkeypatch: Any) -> None:
    daemon = _daemon([_registration()])
    monkeypatch.setattr(daemon, "_resolve_companion_desired", lambda desired: desired.clear())
    try:
        assert daemon._desired() == {}
        snapshot = daemon.pool_source_snapshot()
        assert snapshot.complete and len(snapshot.definitions) == 1
        monkeypatch.setattr(daemon.client, "list_registrations", lambda **kwargs: pytest.fail("unexpected I/O"))
        assert daemon.pool_source_snapshot() is snapshot
        assert isinstance(snapshot.definitions[0], PoolInstanceDefinition)
    finally:
        daemon.shutdown()


def test_initial_snapshot_is_pending_not_empty_complete() -> None:
    daemon = _daemon()
    try:
        assert not daemon.pool_source_snapshot().complete
        assert "collection-pending" in daemon.pool_source_snapshot().reasons
    finally:
        daemon.shutdown()


def test_actual_registrar_retained_declarations_are_not_complete_and_recovery_is_fresh(tmp_path: Any) -> None:
    from agent_dispatch.registrar_discovery import RegistrarSources, add_pointer

    trusted = tmp_path / "trusted"
    trusted.mkdir()
    (trusted / "worker.json").write_text(
        json.dumps({"name": "worker", "owner": "repo:example", "labels": ["worker"]}),
        encoding="utf-8",
    )
    base = tmp_path / "registrar"
    add_pointer("trusted", trusted, base=base)
    pointer_file = base / "pointers.json"
    original = pointer_file.read_text(encoding="utf-8")
    sources = RegistrarSources(
        base=base, dropins=tmp_path / "absent-registrar.d",
        activation_source=lambda: pytest.fail("no plugin candidate needs activation"),
    )
    daemon = _daemon(declared_source=lambda: read_supervisor_declarations(sources))
    try:
        assert daemon._desired()
        assert daemon.pool_source_snapshot().complete
        pointer_file.write_text("{not json", encoding="utf-8")
        assert daemon._desired()
        assert sources.last_report.trusted_authority is ScanAuthority.INDETERMINATE
        assert not daemon.pool_source_snapshot().complete
        assert not daemon.pool_source_snapshot().definitions
        pointer_file.write_text(original, encoding="utf-8")
        assert daemon._desired()
        assert daemon.pool_source_snapshot().complete
    finally:
        daemon.shutdown()


def test_prewithdrawal_registry_result_cannot_be_complete() -> None:
    row = _registration()
    daemon = _daemon([row])
    try:
        daemon._desired()
        publisher = BoundedDeclarationPublisher()
        publisher.withdrawal_epoch = 1
        daemon._declaration_publisher = publisher
        future: Future = Future()
        future.set_result((0, [row], copy.deepcopy([row])))
        daemon._discovery_futures["registrations"] = future
        daemon._reconcile_deadline = time.monotonic() + 1
        assert daemon._desired()
        assert "registrations:prewithdrawal-observation" in daemon.pool_source_snapshot().reasons
        assert not daemon.pool_source_snapshot().definitions
    finally:
        daemon.shutdown()


def test_registry_failure_invalidates_complete_snapshot_without_becoming_empty(monkeypatch: Any) -> None:
    daemon = _daemon([_registration()])
    try:
        daemon._desired()
        assert daemon.pool_source_snapshot().complete

        def failed_read(**kwargs: Any) -> list:
            raise ValueError("invalid-provider-response")

        monkeypatch.setattr(daemon.client, "list_registrations", failed_read)
        with pytest.raises(ValueError, match="invalid-provider-response"):
            daemon._desired()
        assert not daemon.pool_source_snapshot().complete
        assert not daemon.pool_source_snapshot().definitions
        assert daemon.pool_source_snapshot().attempt == 2
    finally:
        daemon.shutdown()
