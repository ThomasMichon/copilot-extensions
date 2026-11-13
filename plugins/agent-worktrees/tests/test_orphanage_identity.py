"""Ordinary orphan writes omit naming while preserving original obligations."""

from __future__ import annotations

import pytest
import yaml

from agent_worktrees import config as cfg, tracking

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
