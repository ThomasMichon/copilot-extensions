"""Retention authority, crash recovery, and real root-lock process boundaries."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent_dispatch import managed_retention
from agent_dispatch.companion import (
    CompanionResolution,
    ManagedLaunchSnapshot,
    companion_receipt_path,
    _command_digest,
)
from agent_dispatch.managed_retention import (
    ManagedRuntimeRetention,
    RetentionPolicy,
    process_identity_domain,
)
from agent_dispatch.managed_runtime import (
    RECEIPT_NAME,
    ManagedRuntimeError,
    ManagedRuntimeLockTimeout,
    ManagedRuntimeMaterializer,
    _canonical_digest,
    _cell_key,
    _quarantine_cell,
    _RootLock,
)
from agent_dispatch.procutil import no_window_kwargs
from tests.test_managed_companion import Harness
from tests.test_managed_runtime import FakeRunner, _policy, _project, _registration


class RetentionHarness:
    def __init__(self, tmp_path):
        self.plugin = _project(tmp_path)
        self.policy = _policy(tmp_path)
        self.materializer = ManagedRuntimeMaterializer(self.policy, runner=FakeRunner())
        self.state = tmp_path / "supervisor"
        self.tokens = {os.getpid(): "supervisor", 100: "child"}
        self.retention = self.make_retention()

    def make_retention(self, *, domain="a" * 64, policy=RetentionPolicy(0, 0)):
        return ManagedRuntimeRetention(
            self.policy.root,
            policy=policy,
            domain_source=lambda: domain,
            token_source=self.tokens.get,
            process_exists=lambda pid: pid in self.tokens,
            group_exists=lambda pid: False,
        )

    def generation(self, version):
        registration = _registration(self.plugin)
        registration["spec"]["managed_runtime"]["runtimes"][0]["version"] = version
        runtime = self.materializer.materialize(registration)
        resolution = CompanionResolution(
            registration,
            (sys.executable, "-c", "pass"),
            None,
            (sys.executable, "-c", "pass"),
            str(self.plugin),
            {},
            1,
            1,
            1,
        )
        return ManagedLaunchSnapshot.capture(resolution, runtime)

    def record_paths(self, kind="leases"):
        return list((self.policy.root / ".retention" / kind).iterdir())


@pytest.fixture
def retention_harness(tmp_path):
    return RetentionHarness(tmp_path)


@pytest.mark.parametrize("kind", ["preparation", "process", "other-environment", "foreign-domain"])
def test_managed_retention_preserves_active_and_foreign_leases(retention_harness, kind):
    h = retention_harness
    snapshot = h.generation("1")
    keeper = h.make_retention(domain="b" * 64) if kind == "foreign-domain" else h.retention
    state = h.state.with_name("other-environment") if kind == "other-environment" else h.state
    if kind == "preparation":
        keeper.prepare(snapshot, state)
    else:
        keeper.launched(snapshot, state, 100, "child")
    if kind == "foreign-domain":
        h.tokens.clear()  # Local PID absence says nothing about another PID authority.
    result = h.retention.cleanup()
    assert result.deleted == ()
    assert result.preserved == (snapshot.runtimes[0].cell,)
    assert result.stale_leases == 0


@pytest.mark.parametrize("kind", ["preparation", "process"])
def test_managed_retention_reclaims_only_provably_stale_leases(retention_harness, kind):
    h = retention_harness
    snapshot = h.generation("1")
    if kind == "preparation":
        h.retention.prepare(snapshot, h.state)
    else:
        h.retention.launched(snapshot, h.state, 100, "child")
    h.tokens.clear()
    result = h.retention.cleanup()
    assert result.deleted == (snapshot.runtimes[0].cell,)
    assert result.stale_leases == 1
    assert h.record_paths() == []


@pytest.mark.parametrize("ambiguity", ["reused", "unverifiable", "group", "probe-error"])
def test_managed_retention_identity_ambiguity_preserves_entire_batch(retention_harness, ambiguity):
    h = retention_harness
    snapshots = [h.generation("1"), h.generation("2")]
    h.retention.launched(snapshots[0], h.state, 100, "child")
    if ambiguity == "reused":
        h.tokens[100] = "reused-pid"
    elif ambiguity == "unverifiable":
        h.tokens[100] = None
    elif ambiguity == "group":
        h.tokens.pop(100)
        h.retention.group_exists = lambda pid: True
    else:

        def unavailable(pid):
            raise PermissionError("process inspection denied")

        h.retention.token_source = unavailable
    with pytest.raises((ManagedRuntimeError, PermissionError)):
        h.retention.cleanup()
    assert all(snapshot.runtimes[0].cell.exists() for snapshot in snapshots)
    assert len(h.record_paths()) == 1


def test_managed_retention_selected_and_rollback_survive_restart(retention_harness):
    h = retention_harness
    old, selected, unused = [h.generation(str(index)) for index in range(3)]
    h.retention.select(old, h.state)
    h.retention.select(selected, h.state)
    h.tokens.clear()
    restarted = h.make_retention()
    result = restarted.cleanup()
    assert result.deleted == (unused.runtimes[0].cell,)
    assert set(result.preserved) == {old.runtimes[0].cell, selected.runtimes[0].cell}
    restarted.select(selected, h.state)
    assert restarted.cleanup().deleted == ()
    restarted.forget(selected.to_dict()["registration"]["id"], h.state)
    assert set(restarted.cleanup().deleted) == {old.runtimes[0].cell, selected.runtimes[0].cell}


def test_managed_retention_foreign_environment_cannot_withdraw_selection(retention_harness):
    h = retention_harness
    snapshot = h.generation("1")
    h.retention.select(snapshot, h.state)
    h.retention.forget(snapshot.to_dict()["registration"]["id"], h.state.with_name("foreign"))
    h.make_retention(domain="b" * 64).forget(snapshot.to_dict()["registration"]["id"], h.state)
    assert h.retention.cleanup().deleted == ()


def test_managed_retention_count_and_age_bounds_do_not_count_protected_cells(retention_harness):
    h = retention_harness
    snapshots = [h.generation(str(index)) for index in range(6)]
    now = time.time()
    for index, snapshot in enumerate(snapshots):
        os.utime(snapshot.runtimes[0].receipt, (now - 1000 + index, now - 1000 + index))
    h.retention.select(snapshots[0], h.state)
    os.utime(snapshots[1].runtimes[0].receipt, (now, now))
    bounded = h.make_retention(policy=RetentionPolicy(2, 500))
    bounded.clock = lambda: now
    result = bounded.cleanup()
    assert set(result.deleted) == {snapshots[index].runtimes[0].cell for index in (2, 3, 4)}
    assert set(result.preserved) == {snapshots[index].runtimes[0].cell for index in (0, 1, 5)}


@pytest.mark.parametrize("failure", ["transient", "locked", "interrupted", "unexpected"])
def test_managed_retention_partial_deletion_never_wedges_published_cells(
    retention_harness, tmp_path, monkeypatch, caplog, failure
):
    h = retention_harness
    first = h.generation("1")
    h.plugin = _project(tmp_path / "unrelated")
    second = h.generation("2")
    snapshots = {snapshot.runtimes[0].cell: snapshot for snapshot in (first, second)}
    assert first.runtimes[0].cell.parent != second.runtimes[0].cell.parent
    deleting = h.policy.root / ".deleting"
    real_rmtree = shutil.rmtree
    attempts = {}
    victim = []
    sleeps = []

    def partial_delete(path, *args, **kwargs):
        if path.parent != deleting:
            return real_rmtree(path, *args, **kwargs)
        assert path.is_relative_to(h.policy.root)
        with pytest.raises(ManagedRuntimeLockTimeout):
            with _RootLock(h.policy.root, timeout=0):
                pytest.fail("recursive deletion must hold the root lock")
        attempts[path] = attempts.get(path, 0) + 1
        if not victim:
            receipt = json.loads((path / RECEIPT_NAME).read_text(encoding="utf-8"))
            original = Path(receipt["ownership"]["cell"])
            assert original in snapshots and not original.exists()
            victim.append((path, original))
            (path / RECEIPT_NAME).unlink()
        if path == victim[0][0] and (failure != "transient" or attempts[path] == 1):
            if failure == "interrupted":
                raise KeyboardInterrupt("recursive deletion interrupted")
            if failure == "unexpected":
                raise RuntimeError("unexpected deletion error")
            raise PermissionError("antivirus has locked a remaining file")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(managed_retention.shutil, "rmtree", partial_delete)
    monkeypatch.setattr(managed_retention.time, "sleep", sleeps.append)
    aborted = failure in ("interrupted", "unexpected")
    if aborted:
        exception = KeyboardInterrupt if failure == "interrupted" else RuntimeError
        with pytest.raises(exception):
            h.retention.cleanup()
        assert {cell for cell in snapshots if cell.exists()} == set(snapshots) - {victim[0][1]}
    else:
        result = h.retention.cleanup()
        assert set(result.deleted) == set(snapshots)
        assert result.preserved == ()
    staging, original = victim[0]
    assert not original.exists()
    assert attempts[staging] == {"transient": 2, "locked": 3}.get(failure, 1)
    assert sleeps == {"transient": [0.1], "locked": [0.1, 0.2]}.get(failure, [])
    if failure == "locked":
        assert "preserving deletion staging residue" in caplog.text
        assert str(staging) in caplog.text
    residue = list(deleting.iterdir())
    assert residue == ([] if failure == "transient" else [staging])
    contents = {
        path.relative_to(deleting): path.read_bytes()
        for path in deleting.rglob("*") if path.is_file()
    }
    before = attempts.copy()
    result = h.make_retention().cleanup()
    assert set(result.deleted) == (set(snapshots) - {original} if aborted else set())
    assert attempts[staging] == before[staging]
    assert all(not cell.exists() for cell in snapshots)

    snapshot = snapshots[original]
    restored = h.materializer.materialize(snapshot.resolution().registration)
    assert restored[0].cell == original
    h.materializer.validate(snapshot.resolution().registration, restored)
    assert h.make_retention().cleanup().deleted == (original,)
    assert len(attempts) == 3  # Rematerialization gets a fresh deletion destination.
    assert attempts[staging] == before[staging]
    assert list(deleting.iterdir()) == residue
    assert {
        path.relative_to(deleting): path.read_bytes()
        for path in deleting.rglob("*") if path.is_file()
    } == contents


def test_managed_retention_failed_move_preserves_cell_and_processes_peers(
    retention_harness, monkeypatch, caplog
):
    h = retention_harness
    snapshot, peer = h.generation("1"), h.generation("2")
    cell = snapshot.runtimes[0].cell
    os.utime(snapshot.runtimes[0].receipt, (2, 2))
    os.utime(peer.runtimes[0].receipt, (1, 1))
    real_replace = os.replace

    def blocked_move(source, destination):
        if source == cell:
            raise PermissionError("cell is locked")
        return real_replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(managed_retention.os, "replace", blocked_move)
        result = h.retention.cleanup()
    assert result.deleted == (peer.runtimes[0].cell,)
    assert result.preserved == (cell,)
    assert "cannot unpublish managed cell; preserving" in caplog.text
    assert cell.exists() and not peer.runtimes[0].cell.exists()
    assert list((h.policy.root / ".deleting").iterdir()) == []
    h.materializer.validate(snapshot.resolution().registration, snapshot.runtimes)
    assert h.materializer.materialize(snapshot.resolution().registration) == snapshot.runtimes
    assert h.make_retention().cleanup().deleted == (cell,)


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_managed_retention_deletion_destination_collision_preserves_residue(
    retention_harness, monkeypatch, kind
):
    h = retention_harness
    cell = h.generation("1").runtimes[0].cell
    identity = managed_retention.uuid.UUID(int=1)
    staging = h.policy.root / ".deleting" / identity.hex
    staging.parent.mkdir()
    if kind == "file":
        staging.write_text("preserve", encoding="utf-8")
    else:
        staging.mkdir()
    monkeypatch.setattr(managed_retention.uuid, "uuid4", lambda: identity)
    with pytest.raises(ManagedRuntimeError, match="destination already exists"):
        h.retention.cleanup()
    assert cell.exists()
    if kind == "file":
        assert staging.read_text(encoding="utf-8") == "preserve"
    else:
        assert staging.is_dir() and list(staging.iterdir()) == []


@pytest.mark.parametrize(
    "policy",
    [
        (-1, 0),
        (101, 0),
        (True, 0),
        (1, -1),
        (1, float("nan")),
        (1, float("inf")),
        (1, True),
    ],
)
def test_managed_retention_rejects_unbounded_policy(policy):
    with pytest.raises(ValueError):
        RetentionPolicy(*policy)


@pytest.mark.parametrize(
    "metadata",
    [
        "lease-json",
        "duplicate-key",
        "lease-version",
        "lease-pid",
        "lease-domain",
        "lease-path",
        "lease-reference",
        "selection-json",
        "cell-json",
        "cell-path",
        "cell-owner",
        "cell-authority",
        "cell-digest",
        "cell-schema",
        "missing-receipt",
    ],
)
def test_managed_retention_malformed_metadata_preserves_all_cells(retention_harness, metadata):
    h = retention_harness
    snapshots = [h.generation("1"), h.generation("2")]
    h.retention.launched(snapshots[0], h.state, 100, "child")
    h.retention.select(snapshots[0], h.state)
    lease = h.record_paths()[0]
    cell = snapshots[1].runtimes[0].cell
    path = cell / RECEIPT_NAME if metadata.startswith("cell") else lease
    if metadata == "selection-json":
        path = h.record_paths("selections")[0]
    if metadata == "missing-receipt":
        (cell / RECEIPT_NAME).unlink()
    elif metadata.endswith("json"):
        path.write_text("{", encoding="utf-8")
    elif metadata == "duplicate-key":
        path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    elif metadata in ("lease-path", "cell-path"):
        target = lease if metadata == "lease-path" else cell
        target.rename(
            target.with_name(
                "f" * (64 if metadata == "lease-path" else 40)
                + (".json" if metadata == "lease-path" else "")
            )
        )
    else:
        record = json.loads(path.read_text(encoding="utf-8"))
        if metadata == "lease-version":
            record["schema_version"] = True
        elif metadata == "lease-pid":
            record["holder"]["pid"] = True
        elif metadata == "lease-domain":
            record["holder"]["domain"] = "foreign"
        elif metadata == "lease-reference":
            record["reference"]["cells"][0]["receipt_digest"] = "0" * 64
        elif metadata == "cell-owner":
            record["ownership"]["cell"] = str(h.plugin)
        elif metadata == "cell-authority":
            record["ownership"]["authority"]["plugin_owner"] = "another@example"
        elif metadata == "cell-digest":
            record["cell_digest"] = "0" * 64
        elif metadata == "cell-schema":
            record["schema_version"] = 9
        path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ManagedRuntimeError):
        h.retention.cleanup()
    assert snapshots[0].runtimes[0].cell.exists()
    assert len(list((h.policy.root / "cells" / cell.parent.name).iterdir())) == 2


def _link(link, target, *, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError):
        if os.name != "nt" or not directory:
            pytest.skip("filesystem links are unavailable")
        subprocess.run(
            [os.environ["COMSPEC"], "/c", "mklink", "/J", str(link), str(target)],
            check=True,
            capture_output=True,
            **no_window_kwargs(),
        )


@pytest.mark.parametrize(
    "location",
    [
        "root", "cells", "owner", "cell", "runtime", "receipt", "lease", "lock",
        "deletion-staging", "deletion-residue",
    ],
)
def test_managed_retention_never_follows_linked_or_reparse_paths(
    retention_harness, location, tmp_path
):
    h = retention_harness
    snapshot = h.generation("1")
    h.retention.prepare(snapshot, h.state)
    runtime = snapshot.runtimes[0]
    residue = h.policy.root / ".deleting" / ("a" * 32)
    residue.mkdir(parents=True)
    (residue / RECEIPT_NAME).write_text("{", encoding="utf-8")
    paths = {
        "root": h.policy.root,
        "cells": h.policy.root / "cells",
        "owner": runtime.cell.parent,
        "cell": runtime.cell,
        "runtime": runtime.cell / "runtime",
        "receipt": runtime.receipt,
        "lease": h.record_paths()[0],
        "lock": h.policy.root / ".materialize.lock",
        "deletion-staging": residue.parent,
        "deletion-residue": residue,
    }
    path = paths[location]
    directory = path.is_dir()
    external = tmp_path / f"outside-{location}"
    path.rename(external)
    _link(path, external, directory=directory)
    before = (
        sorted(str(item.relative_to(external)) for item in external.rglob("*"))
        if directory
        else external.read_bytes()
    )
    with pytest.raises(ManagedRuntimeError, match="link or reparse"):
        h.retention.cleanup()
    after = (
        sorted(str(item.relative_to(external)) for item in external.rglob("*"))
        if directory
        else external.read_bytes()
    )
    assert before == after


def test_managed_retention_revalidates_deletion_staging_before_retry(
    retention_harness, tmp_path, monkeypatch
):
    h = retention_harness
    cell = h.generation("1").runtimes[0].cell
    external = tmp_path / "outside-deletion"
    external.mkdir()
    sentinel = external / "preserve.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    attempts = []

    def linked_residue(staging):
        attempts.append(staging)
        assert not cell.exists()
        (staging / RECEIPT_NAME).unlink()
        _link(staging / "redirect", external, directory=True)
        raise PermissionError("retry with changed staging")

    monkeypatch.setattr(managed_retention.shutil, "rmtree", linked_residue)
    with pytest.raises(ManagedRuntimeError, match="link or reparse"):
        h.retention.cleanup()
    assert len(attempts) == 1
    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert list(external.iterdir()) == [sentinel]


def test_managed_retention_legacy_cells_are_recoverable_but_not_reclaimable(retention_harness):
    h = retention_harness
    snapshot = h.generation("1")
    runtime = snapshot.runtimes[0]
    record = json.loads(runtime.receipt.read_text(encoding="utf-8"))
    record.pop("ownership")
    record["schema_version"] = 1
    runtime.receipt.write_text(json.dumps(record), encoding="utf-8")
    legacy = runtime.cell.with_name(_cell_key(record))
    runtime.cell.rename(legacy)
    value = snapshot.to_dict()
    value["runtimes"][0].update(
        cell=str(legacy),
        receipt=str(legacy / RECEIPT_NAME),
        python=str(legacy / "runtime" / "bin" / "python"),
    )
    value["environment"]["EXAMPLE_MANAGED_PYTHON"] = value["runtimes"][0]["python"]
    legacy_snapshot = ManagedLaunchSnapshot.from_dict(value)
    h.materializer.validate(legacy_snapshot.resolution().registration, legacy_snapshot.runtimes)
    assert h.retention.cleanup().preserved == (legacy,)
    current = h.materializer.materialize(snapshot.resolution().registration)[0]
    assert current.cell != legacy
    assert legacy.exists()


def test_managed_retention_real_identity_domain_is_stable():
    assert len(process_identity_domain()) == 64
    assert process_identity_domain() == process_identity_domain()


def test_managed_retention_cutover_leases_precede_stop_and_process_gate(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    retention = h.controller._managed_retention()
    retention.policy = RetentionPolicy(0, 0)
    h.daemon.reconcile_once()
    old = h.unit.companion_resolution.managed_snapshot
    original_stop = h.controller.stop
    original_launch = h.launch

    def stop(resolution, process):
        deleted = retention.cleanup().deleted
        assert old.runtimes[0].cell not in deleted
        assert resolution.managed_snapshot.runtimes[0].cell not in deleted
        leases = list((retention.root / ".retention" / "leases").iterdir())
        assert any(json.loads(path.read_text())["role"] == "preparation" for path in leases)
        return original_stop(resolution, process)

    def launch(resolution):
        process = original_launch(resolution)
        original_release = process.release

        def release():
            assert h.controller._receipt_path(h.rid).exists()
            records = [
                json.loads(path.read_text())
                for path in (retention.root / ".retention" / "leases").iterdir()
            ]
            assert any(
                record["role"] == "process" and record["holder"]["pid"] == process.pid
                for record in records
            )
            deleted = retention.cleanup().deleted
            assert old.runtimes[0].cell not in deleted
            assert resolution.managed_snapshot.runtimes[0].cell not in deleted
            original_release()

        process.release = release
        return process

    monkeypatch.setattr(h.controller, "stop", stop)
    monkeypatch.setattr("agent_dispatch.companion._launch_gated", launch)
    h.change()
    h.unhealthy.add("new")
    assert h.daemon.reconcile_once().revived == [h.rid]
    assert h.unit.companion_resolution.managed_snapshot == old
    assert old.runtimes[0].cell.exists()


def test_managed_retention_restart_and_malformed_external_selection(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    h.daemon.reconcile_once()
    snapshot = h.unit.companion_resolution.managed_snapshot
    h.controller.stop(h.unit.companion_resolution, h.unit.proc)
    h.controller = h.make_controller()
    retention = h.controller._managed_retention()
    retention.policy = RetentionPolicy(0, 0)
    assert retention.cleanup().deleted == ()
    h.daemon = h.make_daemon()
    assert h.daemon.reconcile_once().started == [h.rid]
    assert h.unit.companion_resolution.managed_snapshot == snapshot
    h.controller._selection_path(h.rid).write_text("{", encoding="utf-8")
    with pytest.raises(ManagedRuntimeError):
        retention.cleanup()
    assert snapshot.runtimes[0].cell.exists()


def _interrupted_launch(h, snapshot):
    resolution = snapshot.resolution()
    rid = resolution.registration["id"]
    h.retention.launched(snapshot, h.state, 100, "child")
    path = companion_receipt_path(h.state, rid)
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "registration_id": rid,
                "fingerprint": snapshot.fingerprint,
                "managed_snapshot": snapshot.to_dict(),
                "pid": 100,
                "start_token": "child",
                "command_digest": _command_digest(resolution.command),
                "runtime_revision": resolution.registration["runtime_revision"],
                "containment": "windows-job" if os.name == "nt" else "posix-process-group",
            }
        ),
        encoding="utf-8",
    )
    h.tokens.clear()


@pytest.mark.parametrize("role", ["preparation", "process"])
def test_managed_retention_interrupted_scope_protects_every_retained_lease(retention_harness, role):
    h = retention_harness
    older, snapshot, unused = [h.generation(str(index)) for index in range(3)]
    if role == "preparation":
        h.retention.prepare(older, h.state)
    else:
        h.retention.launched(older, h.state, 100, "child")
    _interrupted_launch(h, snapshot)
    for _ in range(2):
        result = h.make_retention().cleanup()
        assert older.runtimes[0].cell.exists()
        assert snapshot.runtimes[0].cell.exists()
        assert not unused.runtimes[0].cell.exists()
        assert result.stale_leases == 0
        assert len(h.record_paths()) == 2
    companion_receipt_path(h.state, snapshot.resolution().registration["id"]).unlink()
    assert set(h.make_retention().cleanup().deleted) == {
        older.runtimes[0].cell, snapshot.runtimes[0].cell,
    }
    assert h.record_paths() == []


@pytest.mark.parametrize("damage", ["missing-cell", "receipt-digest", "opaque-reference"])
def test_managed_retention_invalid_stale_reference_is_preserved_without_wedging(
    retention_harness, tmp_path, caplog, damage
):
    h = retention_harness
    older, snapshot = [h.generation(str(index)) for index in range(2)]
    h.retention.prepare(older, h.state)
    lease = h.record_paths()[0]
    if damage == "missing-cell":
        shutil.rmtree(older.runtimes[0].cell)
    else:
        record = json.loads(lease.read_text())
        if damage == "receipt-digest":
            record["reference"]["cells"][0]["receipt_digest"] = "invalid"
        else:
            record["reference"]["cells"] = None
        identity = {key: record[key] for key in ("scope", "reference", "holder", "role")}
        lease.unlink()
        lease = lease.with_name(f"{_canonical_digest(identity)}.json")
        lease.write_text(json.dumps(record), encoding="utf-8")
    _interrupted_launch(h, snapshot)
    other = tmp_path / "other"
    other.mkdir()
    h.plugin = _project(other)
    unused = h.generation("3")
    before = lease.read_bytes()
    for _ in range(2):
        result = h.make_retention().cleanup()
        assert result.stale_leases == 0
        assert lease.read_bytes() == before
        assert older.runtimes[0].cell.exists() == (damage != "missing-cell")
        assert snapshot.runtimes[0].cell.exists()
        assert unused.runtimes[0].cell.exists() == (damage == "opaque-reference")
    assert "preserving stale managed lease with invalid reference" in caplog.text


@pytest.mark.parametrize("receipt_kind", ["process", "selected"])
@pytest.mark.parametrize("damage", ["missing-cell", "cell-json", "quarantined-cell", "opaque-cell"])
def test_managed_retention_invalid_launch_cell_does_not_wedge_cleanup(
    retention_harness, tmp_path, caplog, receipt_kind, damage
):
    h = retention_harness
    older, snapshot, spare = [h.generation(str(index)) for index in range(3)]
    h.retention.prepare(older, h.state)
    if receipt_kind == "selected":
        h.retention.select(older, h.state)  # Root pin and last-ready receipt may disagree.
    other = tmp_path / "other"
    other.mkdir()
    h.plugin = _project(other)
    unused = h.generation("3")
    h.retention.prepare(unused, h.state.with_name("other-supervisor"))
    _interrupted_launch(h, snapshot)
    path = companion_receipt_path(h.state, snapshot.resolution().registration["id"])
    record = json.loads(path.read_text())
    if receipt_kind == "selected":
        path.unlink()
        path = path.with_suffix(".managed-launch.json")
        record = {
            key: record[key]
            for key in ("schema_version", "registration_id", "fingerprint", "managed_snapshot")
        }
    cell = snapshot.runtimes[0].cell
    damaged_receipt = None
    if damage == "missing-cell":
        shutil.rmtree(cell)
    elif damage == "cell-json":
        damaged_receipt = cell / RECEIPT_NAME
        damaged_receipt.write_text("{", encoding="utf-8")
    elif damage == "quarantined-cell":
        damaged_receipt = _quarantine_cell(h.policy.root, cell) / RECEIPT_NAME
    else:
        record["managed_snapshot"]["runtimes"][0]["cell"] = str(h.policy.root / "ambiguous")
    path.write_text(json.dumps(record), encoding="utf-8")
    receipt_before = path.read_bytes()
    damaged_before = damaged_receipt.read_bytes() if damaged_receipt else None
    retained_leases = {
        lease: lease.read_bytes()
        for lease in h.record_paths()
        if json.loads(lease.read_text())["scope"]["receipt_dir"] == str(h.state)
    }
    selections = (
        {pin: pin.read_bytes() for pin in h.record_paths("selections")}
        if receipt_kind == "selected" else {}
    )
    for attempt in range(3):
        result = h.make_retention().cleanup()
        assert result.deleted == (
            (unused.runtimes[0].cell,) if attempt == 0 and damage != "opaque-cell" else ()
        )
        assert result.stale_leases == (1 if attempt == 0 else 0)
        assert len(h.record_paths()) == len(retained_leases) == 2
        assert all(lease.read_bytes() == before for lease, before in retained_leases.items())
        assert all(pin.read_bytes() == before for pin, before in selections.items())
        assert path.read_bytes() == receipt_before
        assert cell.exists() == (damage in ("cell-json", "opaque-cell"))
        assert older.runtimes[0].cell in result.preserved
        assert spare.runtimes[0].cell in result.preserved
        assert older.runtimes[0].cell.exists()
        assert spare.runtimes[0].cell.exists()
        assert unused.runtimes[0].cell.exists() == (damage == "opaque-cell")
        if damaged_receipt:
            assert damaged_receipt.read_bytes() == damaged_before
    assert "preserving managed launch receipt with invalid reference" in caplog.text
    assert str(path) in caplog.text


@pytest.mark.parametrize("error_type", [RuntimeError, TypeError])
def test_managed_retention_launch_reference_unexpected_errors_propagate(
    retention_harness, monkeypatch, error_type
):
    h = retention_harness
    snapshot, unused = [h.generation(str(index)) for index in range(2)]
    _interrupted_launch(h, snapshot)

    def invalid_reference(snapshot):
        raise error_type("unexpected reference failure")

    monkeypatch.setattr(h.retention, "_reference", invalid_reference)
    with pytest.raises(error_type, match="unexpected reference failure"):
        h.retention.cleanup()
    assert snapshot.runtimes[0].cell.exists()
    assert unused.runtimes[0].cell.exists()
    assert len(h.record_paths()) == 1


def test_managed_retention_root_selection_survives_external_publish_failure(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    h.daemon.reconcile_once()
    old = h.unit.companion_resolution.managed_snapshot
    h.change()
    snapshot = ManagedLaunchSnapshot.capture(
        h.controller.resolve(h.registration, machine="machine-a", env="default"),
        h.materializer.materialize(h.registration),
    )
    retention = h.controller._managed_retention()
    retention.select(snapshot, h.state)  # Crash before last-ready marker publication.
    retention.policy = RetentionPolicy(0, 0)
    assert retention.cleanup().deleted == ()
    assert h.controller.selected_managed(h.rid) == old
    record = json.loads(next((retention.root / ".retention" / "selections").iterdir()).read_text())
    assert record["selected"]["launch_digest"] == snapshot.fingerprint
    assert record["rollback"]["launch_digest"] == old.fingerprint


def test_managed_retention_unrelated_corruption_cannot_break_cutover(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    h.daemon.reconcile_once()
    retention = h.controller._managed_retention()
    (retention.root / ".retention" / "leases" / "foreign.json").write_text("{", encoding="utf-8")
    h.change()
    assert h.daemon.reconcile_once().restarted == [h.rid]
    assert h.unit.companion_resolution.environment["MODE"] == "new"
    assert sum(process.poll() is None for process in h.processes) == 1
    with pytest.raises(ManagedRuntimeError):
        retention.cleanup()


def test_managed_retention_redundant_pin_failure_preserves_ready_process(
    tmp_path, monkeypatch, caplog
):
    h = Harness(tmp_path, monkeypatch)
    h.daemon.reconcile_once()

    def cannot_release(*args):
        raise OSError("lease storage unavailable")

    monkeypatch.setattr(h.controller._managed_retention(), "release_preparation", cannot_release)
    h.change()
    assert h.daemon.reconcile_once().restarted == [h.rid]
    assert h.unit.proc.poll() is None
    assert "preserving redundant managed preparation lease" in caplog.text


@pytest.mark.parametrize("transition", ["first", "replacement", "rollback"])
@pytest.mark.parametrize("failure", ["lock-timeout", "write-error"])
def test_managed_retention_selection_pin_failure_keeps_ready_process_recoverable(
    tmp_path, monkeypatch, caplog, transition, failure
):
    h = Harness(tmp_path, monkeypatch)
    if transition != "first":
        h.daemon.reconcile_once()
        h.change()
    if transition == "rollback":
        h.unhealthy.add("new")
    retention = h.controller._managed_retention()
    original_select = retention.select

    def cannot_select(snapshot, receipt_dir):
        if failure == "lock-timeout":
            lock = _RootLock(retention.root, timeout=0)
            monkeypatch.setattr(lock._lock, "acquire", lambda: False)
            with lock:
                pytest.fail("contended lock must not be acquired")

        def cannot_write(*args):
            raise OSError("selection storage unavailable")

        with monkeypatch.context() as patch:
            patch.setattr(retention, "_write", cannot_write)
            original_select(snapshot, receipt_dir)

    monkeypatch.setattr(retention, "select", cannot_select)
    summary = h.daemon.reconcile_once()
    bucket = {"first": "started", "replacement": "restarted", "rollback": "revived"}[transition]
    assert getattr(summary, bucket) == [h.rid]
    snapshot = h.unit.companion_resolution.managed_snapshot
    process = h.unit.proc
    assert process.poll() is None
    assert sum(p.poll() is None for p in h.processes) == 1
    assert h.controller.selected_managed(h.rid) == snapshot
    receipt = json.loads(h.controller._receipt_path(h.rid).read_text())
    assert receipt["pid"] == process.pid
    assert receipt["fingerprint"] == snapshot.fingerprint
    leases = [
        json.loads(path.read_text())
        for path in (retention.root / ".retention" / "leases").iterdir()
    ]
    assert any(
        record["role"] == "process" and record["holder"]["pid"] == process.pid
        and record["reference"]["launch_digest"] == snapshot.fingerprint
        for record in leases
    )
    assert "incomplete redundant managed selection pin" in caplog.text
    retention.policy = RetentionPolicy(0, 0)
    for _ in range(2):
        assert snapshot.runtimes[0].cell not in retention.cleanup().deleted
    monkeypatch.setattr(retention, "select", original_select)
    # Restart under the selected authority, including after rollback to the prior version.
    h.registrations = [snapshot.to_dict()["registration"]]
    h.controller = h.make_controller()
    h.daemon = h.make_daemon()
    assert h.daemon.reconcile_once().running == [h.rid]
    assert h.unit.companion_resolution.managed_snapshot == snapshot
    record = json.loads(next((retention.root / ".retention" / "selections").iterdir()).read_text())
    assert record["selected"]["launch_digest"] == snapshot.fingerprint


@pytest.mark.parametrize("recovery", ["launch", "live"])
def test_managed_retention_pin_contention_does_not_strand_recovered_process(
    tmp_path, monkeypatch, caplog, recovery
):
    if recovery == "live" and os.name == "nt":
        pytest.skip("Windows cannot readopt a predecessor's Job")
    h = Harness(tmp_path, monkeypatch)
    h.daemon.reconcile_once()
    snapshot = h.unit.companion_resolution.managed_snapshot
    process = h.unit.proc

    def cannot_select(*args):
        raise ManagedRuntimeLockTimeout("root lock is busy")

    monkeypatch.setattr(h.controller._managed_retention(), "select", cannot_select)
    # Exercise the recovered-launch branch without changing platform containment policy.
    monkeypatch.setattr(h.controller, "_recover", lambda *args: process)
    if recovery == "launch":
        result = h.controller.launch(snapshot.resolution(), fingerprint=snapshot.fingerprint)
    else:
        result = h.controller.recover_live(snapshot)
    assert result.process is process
    assert result.recovered
    assert process.poll() is None
    assert len(h.processes) == 1
    assert h.controller._receipt_path(h.rid).exists()
    assert h.controller.selected_managed(h.rid) == snapshot
    assert "incomplete redundant managed selection pin" in caplog.text


def test_managed_retention_process_lease_failure_still_blocks_gate(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)

    def cannot_lease(*args):
        raise ManagedRuntimeLockTimeout("root lock is busy")

    monkeypatch.setattr(h.controller._managed_retention(), "launched", cannot_lease)
    summary = h.daemon.reconcile_once()
    assert summary.started == summary.running == []
    assert h.processes and all(process.poll() is not None for process in h.processes)
    assert not any(event[0] in ("release", "health") for event in h.events)
    assert not h.controller._receipt_path(h.rid).exists()
    assert h.controller.selected_managed(h.rid) is None


def test_managed_retention_unsafe_selection_metadata_still_fails_launch(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)

    def unsafe_select(*args):
        raise ManagedRuntimeError("managed retention cell contents changed")

    monkeypatch.setattr(h.controller._managed_retention(), "select", unsafe_select)
    summary = h.daemon.reconcile_once()
    assert summary.started == summary.running == []
    assert h.processes and all(process.poll() is not None for process in h.processes)
    assert not h.controller._receipt_path(h.rid).exists()
    assert h.controller.selected_managed(h.rid) is None


def test_managed_retention_unmanaged_successor_does_not_block_collection(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    h.daemon.reconcile_once()
    previous = h.unit.companion_resolution.managed_snapshot
    h.registration["spec"].pop("managed_runtime")
    h.registration["runtime_revision"].pop("managed_runtime")
    assert h.daemon.reconcile_once().restarted == [h.rid]
    assert h.unit.companion_resolution.managed_snapshot is None
    retention = h.controller._managed_retention()
    retention.policy = RetentionPolicy(0, 0)
    assert retention.cleanup().deleted == (previous.runtimes[0].cell,)
    assert h.unit.proc.poll() is None


def test_managed_retention_invalid_pin_does_not_withdraw_external_selection(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    h.daemon.reconcile_once()
    selection = h.controller._selection_path(h.rid)
    before = selection.read_bytes()
    root_selection = next(
        (h.controller._managed_retention().root / ".retention" / "selections").iterdir()
    )
    root_selection.write_text("{", encoding="utf-8")
    with pytest.raises(ManagedRuntimeError):
        h.controller.forget_managed(h.rid)
    assert selection.read_bytes() == before
    assert h.unit.proc.poll() is None


@pytest.mark.skipif(os.name != "nt", reason="native Windows junction boundary")
def test_managed_retention_rejects_native_junction(retention_harness, tmp_path):
    h = retention_harness
    snapshot = h.generation("1")
    runtime_dir = snapshot.runtimes[0].cell / "runtime"
    external = tmp_path / "external-runtime"
    runtime_dir.rename(external)
    subprocess.run(
        [os.environ["COMSPEC"], "/c", "mklink", "/J", str(runtime_dir), str(external)],
        check=True,
        capture_output=True,
        **no_window_kwargs(),
    )
    with pytest.raises(ManagedRuntimeError, match="link or reparse"):
        h.retention.cleanup()
    assert (external / "bin" / "python").read_bytes() == b"python"


def test_managed_retention_rejects_lexical_escape_in_snapshot(retention_harness):
    h = retention_harness
    snapshot = h.generation("1")
    value = snapshot.to_dict()
    value["runtimes"][0]["cell"] = str(h.policy.root / ".." / "plugin")
    altered = ManagedLaunchSnapshot.from_dict(value)
    with pytest.raises(ManagedRuntimeError, match="escapes"):
        h.retention.prepare(altered, h.state)
    assert snapshot.runtimes[0].cell.exists()


def test_managed_retention_root_lock_serializes_real_materialization_process(
    retention_harness, tmp_path
):
    h = retention_harness
    old = h.generation("1")
    marker, release, completed = [tmp_path / name for name in ("building", "release", "cleaned")]
    policy_root = h.policy.root
    # The worker builds through the real materializer and waits while owning its root lock.
    code = (
        "import json, sys, time\n"
        "from pathlib import Path\n"
        "from agent_dispatch.managed_runtime import ManagedRuntimeMaterializer\n"
        "from tests.test_managed_runtime import FakeRunner, _policy, _registration\n"
        "root, plugin, marker, release = map(Path, sys.argv[1:])\n"
        "policy = _policy(root)\n"
        "from dataclasses import replace\n"
        f"policy = replace(policy, root=Path({str(policy_root)!r}))\n"
        "runner = FakeRunner()\n"
        "def run(argv, cwd, environment):\n"
        "    if list(argv)[1:3] == ['pip', 'install']:\n"
        "        marker.touch()\n"
        "        deadline = time.monotonic() + 15\n"
        "        while not release.exists():\n"
        "            if time.monotonic() > deadline: raise RuntimeError('test release timed out')\n"
        "            time.sleep(.02)\n"
        "    runner(argv, cwd, environment)\n"
        "registration = _registration(plugin)\n"
        "registration['spec']['managed_runtime']['runtimes'][0]['version'] = '2'\n"
        "ManagedRuntimeMaterializer(policy, runner=run).materialize(registration)\n"
    )
    worker_root = tmp_path / "worker"
    worker_root.mkdir()
    worker = subprocess.Popen(
        [sys.executable, "-c", code, str(worker_root), str(h.plugin), str(marker), str(release)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **no_window_kwargs(),
    )
    cleaner = None
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and worker.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists(), worker.communicate(timeout=1)
        with pytest.raises(ManagedRuntimeLockTimeout, match="timed out"):
            with _RootLock(policy_root, timeout=0.1):
                pytest.fail("materialization owns the root lock")
        cleaner = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; import sys; "
                "from agent_dispatch.managed_retention import ManagedRuntimeRetention, RetentionPolicy; "
                "ManagedRuntimeRetention(Path(sys.argv[1]), policy=RetentionPolicy(0,0)).cleanup(); "
                "Path(sys.argv[2]).touch()",
                str(policy_root),
                str(completed),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **no_window_kwargs(),
        )
        time.sleep(0.3)
        assert cleaner.poll() is None
        assert not completed.exists()
        assert old.runtimes[0].cell.exists()
        release.touch()
        assert worker.wait(timeout=10) == 0, worker.communicate()
        assert cleaner.wait(timeout=10) == 0, cleaner.communicate()
        assert completed.exists()
        assert not old.runtimes[0].cell.exists()
        assert list((policy_root / ".staging").iterdir()) == []
    finally:
        release.touch()
        for process in (worker, cleaner):
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                process.communicate(timeout=5)
