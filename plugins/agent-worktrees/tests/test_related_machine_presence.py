"""Tests for agent_worktrees.related_machine_presence."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent_worktrees import related, related_machine_presence, state_root
from agent_worktrees.related import Locus, RelatedConfig, RelatedEntry


def _write_entry(tmp_path: Path, name: str, machines: list[str], **locus_kwargs):
    cfg = RelatedConfig(related={
        name: RelatedEntry(
            name=name,
            locus=Locus(preferred="local", machines=list(machines), **locus_kwargs),
        ),
    })
    related.write_related(tmp_path, cfg)


def _bound_config(monkeypatch, knowledge_path: Path):
    monkeypatch.setattr(
        state_root,
        "resolve_state_root",
        lambda *_a, **_k: state_root.StateRoot(
            str(knowledge_path), "knowledge_repo", "dotfiles", True, True, True,
        ),
    )
    return SimpleNamespace()


class TestRecordLocalPresence:
    def test_appends_machine_when_entry_exists_and_not_listed(self, tmp_path, monkeypatch):
        _write_entry(tmp_path, "example-repo", ["dev6", "cloud1"])
        config = _bound_config(monkeypatch, tmp_path)

        changed = related_machine_presence.record_local_presence(
            config, "example-repo", "book2",
        )

        assert changed is True
        entry = related.get_related(tmp_path, "example-repo")
        assert entry.locus.machines == ["dev6", "cloud1", "book2"]

    def test_no_op_when_machine_already_listed(self, tmp_path, monkeypatch):
        _write_entry(tmp_path, "example-repo", ["dev6", "book2"])
        config = _bound_config(monkeypatch, tmp_path)

        changed = related_machine_presence.record_local_presence(
            config, "example-repo", "book2",
        )

        assert changed is False
        entry = related.get_related(tmp_path, "example-repo")
        assert entry.locus.machines == ["dev6", "book2"]

    def test_no_op_when_machine_excluded(self, tmp_path, monkeypatch):
        _write_entry(
            tmp_path, "example-repo", ["dev6"],
            excluded_machines=["cloud2"],
        )
        config = _bound_config(monkeypatch, tmp_path)

        changed = related_machine_presence.record_local_presence(
            config, "example-repo", "cloud2",
        )

        assert changed is False
        entry = related.get_related(tmp_path, "example-repo")
        assert entry.locus.machines == ["dev6"]

    def test_no_op_when_no_existing_related_entry(self, tmp_path, monkeypatch):
        related.write_related(tmp_path, RelatedConfig())
        config = _bound_config(monkeypatch, tmp_path)

        changed = related_machine_presence.record_local_presence(
            config, "never-declared-repo", "book2",
        )

        assert changed is False
        assert related.get_related(tmp_path, "never-declared-repo") is None

    def test_no_op_when_knowledge_repo_not_bound(self, monkeypatch):
        monkeypatch.setattr(
            state_root,
            "resolve_state_root",
            lambda *_a, **_k: state_root.StateRoot(
                None, "knowledge_repo", "dotfiles", True, True, False,
                error="unavailable",
            ),
        )

        changed = related_machine_presence.record_local_presence(
            SimpleNamespace(), "example-repo", "book2",
        )

        assert changed is False

    def test_empty_repo_name_or_machine_is_a_no_op(self, monkeypatch):
        assert related_machine_presence.record_local_presence(
            SimpleNamespace(), "", "book2",
        ) is False
        assert related_machine_presence.record_local_presence(
            SimpleNamespace(), "example-repo", "",
        ) is False

    def test_never_raises_on_internal_failure(self, monkeypatch):
        def _boom(*_a, **_k):
            raise RuntimeError("boom")

        monkeypatch.setattr(state_root, "resolve_state_root", _boom)

        changed = related_machine_presence.record_local_presence(
            SimpleNamespace(), "example-repo", "book2",
        )

        assert changed is False
