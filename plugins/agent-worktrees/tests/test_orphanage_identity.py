"""Ordinary orphan writes omit naming while preserving original obligations."""

from __future__ import annotations

import pytest
import yaml

from agent_worktrees import cleanup, config as cfg, git_ops, tracking

NAMING = {"machine", "platform", "machine_identity_version", "environment", "execution_machine"}


class ReceivingConfig:
    repo_name = "sample"

    @property
    def machine(self) -> str:
        pytest.fail("orphan persistence must not consult a machine name")


@pytest.fixture
def orphanage(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "project_dir", lambda project=None: tmp_path)
    entries = [{
        "kind": "worktree", "ref": "original-space/sample/child",
        "source_worktree": "source", "project": "sample", "disposition": "abandoned",
        "handoff_to": "original-recipient", "abandoned_at": "original-time",
        "machine": "obsolete", "platform": "wsl", "environment": "wsl",
        "machine_identity_version": 99, "execution_machine": "obsolete-guest",
        "unknown_future_field": {"keep": True},
    }]
    path = tracking.orphanage_path("sample")
    path.write_text(yaml.safe_dump({"orphaned": entries}), encoding="utf-8")
    return path, entries


def test_legacy_orphan_read_and_duplicate_rehome_do_not_migrate(orphanage):
    path, entries = orphanage
    before = path.read_bytes(), path.stat().st_mtime_ns
    assert tracking.load_orphaned_obligations_strict("sample") == [
        {key: value for key, value in entries[0].items() if key not in NAMING}
    ]
    assert tracking.rehome_abandoned_obligations(
        [tracking.ResourceClaim(kind="worktree", ref=entries[0]["ref"])],
        source_worktree="source", config=ReceivingConfig(), project="sample",
    ) == []
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


@pytest.mark.parametrize("operation", ["rehome", "remove"])
def test_ordinary_orphan_writes_drop_legacy_naming_and_preserve_evidence(orphanage, operation):
    path, entries = orphanage
    if operation == "rehome":
        added = tracking.rehome_abandoned_obligations(
            [tracking.ResourceClaim(kind="codespace", ref="new-resource")],
            source_worktree="new-source", config=ReceivingConfig(), project="sample",
        )
        assert len(added) == 1 and not NAMING.intersection(added[0])
    else:
        entries.append({"kind": "task", "ref": "finished-task", "source_worktree": "other"})
        path.write_text(yaml.safe_dump({"orphaned": entries}), encoding="utf-8")
        assert tracking.remove_orphaned_obligations([("other", "finished-task")], project="sample") == 1
    written = yaml.safe_load(path.read_text(encoding="utf-8"))["orphaned"]
    assert all(not NAMING.intersection(entry) for entry in written)
    assert written[0] == {key: value for key, value in entries[0].items() if key not in NAMING}


def test_loader_to_cleanup_preserves_foreign_or_unresolved_obligation(tmp_path, monkeypatch):
    from test_creation_tail_repair import _creation_fixture

    creation = _creation_fixture(tmp_path, monkeypatch, native_git=True, tuple_identity=True)
    config = creation.configs["harness"]
    path = tracking.orphanage_path("harness")
    path.write_text(yaml.safe_dump({"orphaned": [{
        "kind": "codespace", "ref": "foreign-resource", "source_worktree": "foreign-source",
        "project": "harness", "machine": "another-host", "handoff_to": "original-recipient",
    }]}), encoding="utf-8")
    original = path.read_bytes()
    calls = []

    def reclaim(*args):
        calls.append(args)
        return cleanup.ReclaimResult("reclaimed", "synthetic callback")

    monkeypatch.setitem(cleanup._RECLAIMERS, "codespace", reclaim)
    rows = cleanup.cleanup_orphanage(config, apply=True, project="harness")
    assert rows[0]["status"] == "skipped"
    assert not calls
    assert path.read_bytes() == original


def _receiving_cleanup_fixture(tmp_path, monkeypatch):
    from test_creation_tail_repair import _creation_fixture

    creation = _creation_fixture(tmp_path, monkeypatch, native_git=True, tuple_identity=True)
    record = tracking.load_record(creation.record.yaml_path)
    record.resources = [tracking.ResourceClaim(kind="codespace", ref="owned-resource", state="released")]
    record.status = "finalized"
    tracking.save_record(record)
    path = tracking.orphanage_path("harness")
    tracking.rehome_abandoned_obligations(
        record.resources, source_worktree=record.worktree_id,
        config=creation.configs["harness"], project="harness", handoff_to="receiver",
    )
    return creation, record, path


@pytest.mark.parametrize("retired_checkout", [False, True])
def test_loader_to_cleanup_uses_receiving_custody_after_rename(
    tmp_path, monkeypatch, retired_checkout,
):
    from test_identity_rename_ownership import rename

    creation, record, path = _receiving_cleanup_fixture(tmp_path, monkeypatch)
    if retired_checkout:
        git_ops.git("worktree", "remove", record.worktree_path, cwd=creation.anchors["harness"])
    rename(creation)
    calls = []

    def reclaim(ref, apply, config):
        calls.append((ref, apply))
        return cleanup.ReclaimResult("reclaimed", "synthetic callback")

    monkeypatch.setitem(cleanup._RECLAIMERS, "codespace", reclaim)
    rows = cleanup.cleanup_orphanage(creation.configs["harness"], apply=True, project="harness")
    assert rows[0]["status"] == "reclaimed", rows
    assert calls == [("owned-resource", True)]
    assert tracking.load_orphaned_obligations("harness") == []


@pytest.mark.parametrize("case", [
    "missing-source", "active-source", "live-claim", "different-ref", "handoff", "foreign-project",
])
def test_loader_to_cleanup_never_reclaims_uncertain_or_live_custody(tmp_path, monkeypatch, case):
    creation, record, path = _receiving_cleanup_fixture(tmp_path, monkeypatch)
    if case == "missing-source":
        record.yaml_path.unlink()
    elif case == "foreign-project":
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["orphaned"][0]["project"] = "knowledge"
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
    else:
        if case == "active-source":
            record.status = "active"
        elif case == "live-claim":
            record.resources[0].state = "active"
        elif case == "different-ref":
            record.resources[0].ref = "another-resource"
        else:
            record.resources[0].handoff_bundle = "accepted-elsewhere"
        tracking.save_record(record, preserve_handoff_reservations=case != "handoff")
    original = path.read_bytes()
    monkeypatch.setitem(
        cleanup._RECLAIMERS, "codespace",
        lambda *_args: pytest.fail("unproven or live custody must not reach a destructive callback"),
    )
    rows = cleanup.cleanup_orphanage(creation.configs["harness"], apply=True, project="harness")
    assert rows[0]["status"] == "skipped"
    assert path.read_bytes() == original


def test_loader_to_cleanup_preserves_colliding_foreign_orphan_entry(tmp_path, monkeypatch):
    creation, _, path = _receiving_cleanup_fixture(tmp_path, monkeypatch)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["orphaned"].append({**data["orphaned"][0], "project": "knowledge", "machine": "foreign"})
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    original = path.read_bytes()
    monkeypatch.setitem(
        cleanup._RECLAIMERS, "codespace",
        lambda *_args: pytest.fail("one cleanup key cannot discharge another owner's entry"),
    )
    rows = cleanup.cleanup_orphanage(creation.configs["harness"], apply=True, project="harness")
    assert len(rows) == 2 and all(row["status"] == "skipped" for row in rows)
    assert path.read_bytes() == original
