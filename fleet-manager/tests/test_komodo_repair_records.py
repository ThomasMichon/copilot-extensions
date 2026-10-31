"""Repair-only shape checks and non-creating persistence primitives."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sqlite3
import sys
from typing import Any

import pytest

from fleet_manager.komodo_repair import KomodoRepairOwner
from fleet_manager.komodo_repair_evidence import read_outcome, validate_history
from fleet_manager.komodo_repair_selection import KomodoRepairSelection
from fleet_manager.komodo_selection import KomodoSelection
from fleet_manager.komodo_state import KomodoState
from fleet_manager.node_journal import NodeJournal
from fleet_manager.operations import TargetReceipt
from fleet_manager.planning import PlanError, canonical, digest
from test_komodo_declaration import intent
from test_komodo_native import native_rig as native_rig
from test_runtime_admission import Harness
from test_runtime_identity import keys as keys


@pytest.mark.parametrize("mode", [
    "dto-resolver", "same-release", "installation", "generation", "compatibility",
    "withdrawal", "runtime-shape", "request-shape", "revision-shape", "expiry-shape",
])
@pytest.mark.skipif(sys.platform != "linux", reason="Linux host owner selection")
def test_repair_records_cannot_appoint_authority_or_change_native_custody(
    native_rig: Any, keys: Any, mode: str,
) -> None:
    rig = native_rig
    root = rig["root"] / "host"
    root.mkdir(mode=0o700)
    candidate = KomodoSelection(
        rig["selected"], "fleet-a", "install-a", 1, "policy-a", 1900001000,
        "a" * 64, root, rig["tooling"], rig["material"])
    executor = replace(candidate, release_digest="b" * 64)
    request = intent("original", generation=1, deployment=candidate.deployment_digest)
    fields: dict[str, Any] = {
        "candidate": candidate, "executor": executor, "request_digest": request.request_digest,
        "executor_runtime": "c" * 64, "transition_reference": "transition-a",
        "transition_revision": 1, "valid_until": 1900001000,
    }
    if mode == "dto-resolver":
        result = KomodoRepairSelection(**fields)
        with pytest.raises(PlanError, match="code-registered"):
            KomodoRepairOwner(resolve=result, admission=Harness(keys).owner,
                              authority="gateway-a", clock=lambda: 1900000000)
    else:
        if mode == "same-release":
            fields["executor"] = candidate
        elif mode in ("installation", "generation", "compatibility"):
            fields["executor"] = replace(executor, **{
                "installation": {"installation": "different-installation"},
                "generation": {"generation": 2},
                "compatibility": {"compatible_with": ("d" * 64,)},
            }[mode])
        elif mode == "withdrawal":
            from fleet_manager.komodo_declaration import KomodoDeclaration

            value = candidate.declaration.to_dict()
            value["desired_state"] = "absent"
            fields["candidate"] = replace(candidate, declaration=KomodoDeclaration.from_dict(value))
            fields["executor"] = replace(fields["candidate"], release_digest="b" * 64)
        else:
            field = {
                "runtime-shape": "executor_runtime", "request-shape": "request_digest",
                "revision-shape": "transition_revision", "expiry-shape": "valid_until",
            }[mode]
            fields[field] = True if field.endswith(("revision", "until")) else "unsupported"
        with pytest.raises(PlanError):
            KomodoRepairSelection(**fields)
    assert not (rig["directory"] / "calls.jsonl").exists()
    assert not (root / candidate.resource).exists()


@pytest.mark.parametrize("state", ["missing", "empty", "older-schema"])
def test_existing_journal_neither_creates_nor_migrates(tmp_path: Path, state: str) -> None:
    path = tmp_path / "node.sqlite"
    if state == "empty":
        path.touch(mode=0o600)
    elif state == "older-schema":
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE original_evidence(value TEXT)")
            connection.execute("INSERT INTO original_evidence VALUES('keep-original')")
            connection.execute("PRAGMA user_version=1")
    before = path.read_bytes() if path.exists() else None
    with pytest.raises((PlanError, sqlite3.OperationalError)):
        NodeJournal(path, authority="gateway-a", installation="install-a", target="host-a",
                    existing_only=True)
    assert (path.read_bytes() if path.exists() else None) == before


@pytest.mark.skipif(sys.platform != "linux", reason="Linux physical owner lock")
def test_existing_physical_owner_never_creates_a_missing_lock(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    resource = digest({"daemon": "daemon-a", "project": "project-a"})
    for present in (False, True):
        if present:
            (runtime / resource).mkdir(mode=0o700)
        with pytest.raises(PlanError, match="existing"):
            with KomodoState.open(runtime, daemon="daemon-a", project="project-a", existing_only=True):
                raise AssertionError("missing original lock was accepted")
        assert (runtime / resource).exists() is present
        assert not (runtime / resource / "owner.lock").exists()
        assert not (runtime / resource / "node.sqlite").exists()


@pytest.mark.parametrize("at", [1, 2])
def test_terminal_checkpoint_withdrawal_rolls_back_the_same_journal(tmp_path: Path, at: int) -> None:
    journal = NodeJournal(tmp_path / "node.sqlite", authority="gateway-a",
                          installation="install-a", target="host-a")
    request = intent("original", generation=1, deployment="a" * 64)
    journal.accept_named_external(request, resource="b" * 64)
    assert journal.begin_external(request)
    before = journal.inspect(request)
    value = request.to_dict()
    del value["parameters"]
    value.update(schema="fleet-manager.target-receipt", status="succeeded", sequence=3,
                 request_digest=request.request_digest, result_ref="c" * 64)
    receipt = TargetReceipt.from_dict(value)
    calls: list[bool] = []

    def checkpoint() -> None:
        calls.append(True)
        assert journal.inspect(request) == before
        if len(calls) == at:
            raise PlanError("fixture current appointment withdrawn")

    with pytest.raises(PlanError, match="withdrawn"):
        journal.reconcile(request, receipt, checkpoint=checkpoint)
    assert len(calls) == at and journal.inspect(request) == before
    assert len(journal.results()) == 2
    assert journal.reconcile(request, receipt, checkpoint=lambda: None) == receipt
    assert journal.reconcile(request, receipt, checkpoint=lambda: None) == receipt
    assert len(journal.results()) == 3


@pytest.mark.parametrize("value", [None, [], {"unowned": "a" * 64}, {"failed": "unsupported"},
                                   {"failed": "a" * 64}])
def test_history_cannot_be_fabricated_from_current_state(tmp_path: Path, value: object) -> None:
    with pytest.raises(PlanError):
        validate_history(tmp_path, value)
    assert not (tmp_path / "receipts").exists()


def test_history_retains_exact_immutable_evidence(tmp_path: Path) -> None:
    receipts = tmp_path / "receipts"
    receipts.mkdir(mode=0o700)
    value = {"native": {"core": "a" * 64}, "deployment": "b" * 64}
    reference = digest(value)
    path = receipts / (reference + ".json")
    path.touch(mode=0o600)
    path.write_bytes(canonical(value))
    assert validate_history(tmp_path, {"failed": reference}) == {"failed": reference}
    assert read_outcome(tmp_path, reference) == value
    path.write_bytes(canonical({**value, "deployment": "c" * 64}))
    with pytest.raises(PlanError, match="missing or changed"):
        read_outcome(tmp_path, reference)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux physical host runtime")
def test_repair_binding_preserves_selected_physical_owner(native_rig: Any) -> None:
    rig = native_rig
    for name in ("original-runtime", "shifted-runtime"):
        (rig["root"] / name).mkdir(mode=0o700)
    candidate = KomodoSelection(
        rig["selected"], "fleet-a", "install-a", 1, "policy-a", 1900001000,
        "a" * 64, rig["root"] / "original-runtime", rig["tooling"], rig["material"])
    executor = replace(candidate, release_digest="b" * 64)
    original = KomodoRepairSelection(candidate, executor, "c" * 64, "d" * 64,
                                     "transition-a", 1, 1900001000)
    shifted = replace(original,
        candidate=replace(candidate, host_runtime=rig["root"] / "shifted-runtime"),
        executor=replace(executor, host_runtime=rig["root"] / "shifted-runtime"))
    assert shifted.candidate.deployment_digest == original.candidate.deployment_digest
    assert shifted.executor.deployment_digest == original.executor.deployment_digest
    assert shifted.candidate.resource == original.candidate.resource
    assert shifted.binding != original.binding
    assert not (rig["directory"] / "calls.jsonl").exists()
