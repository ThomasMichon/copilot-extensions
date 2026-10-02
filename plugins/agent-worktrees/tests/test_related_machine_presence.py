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


class TestRecordOnAdoption:
    """Regression coverage for the ``record_on_adoption`` adoption-site
    helper: it must load *that project's own* config, not whatever project
    happens to be active (or none) -- a bare ``load_config(project=project)``
    resolves its config *path* independently of the ``project`` kwarg and
    raises when no project is active, which register-project-entry's
    adoption flow never sets."""

    def test_uses_project_specific_config_without_an_active_project(
        self, monkeypatch, tmp_path: Path
    ):
        from agent_worktrees import config as cfg

        # No active project is set anywhere in this test -- exactly
        # register-project-entry's own calling context. A regression to
        # `load_config(project=project)` (no explicit path) would resolve
        # `default_config_path()`'s *currently active* project instead of
        # "myproj" (or raise outright with none active), get swallowed by
        # record_on_adoption's try/except, and silently record nothing.
        assert cfg.active_project() is None

        seen: dict[str, object] = {}
        sentinel_config = SimpleNamespace()

        def _fake_load_project_config(name):
            seen["project_config_loaded_for"] = name
            return sentinel_config

        monkeypatch.setattr(cfg, "load_project_config", _fake_load_project_config)
        monkeypatch.setattr(cfg, "detect_machine", lambda *_a, **_k: "book2")

        recorded: dict[str, object] = {}
        monkeypatch.setattr(
            related_machine_presence,
            "record_local_presence",
            lambda config, repo_name, machine, *, cwd=None: recorded.update(
                config=config, repo_name=repo_name, machine=machine, cwd=cwd,
            )
            or True,
        )

        changed = related_machine_presence.record_on_adoption(
            "myproj", str(tmp_path),
        )

        assert changed is True
        assert seen == {"project_config_loaded_for": "myproj"}
        assert recorded == {
            "config": sentinel_config, "repo_name": "myproj",
            "machine": "book2", "cwd": str(tmp_path),
        }

    def test_prefers_loaded_config_machine_over_redetection(
        self, monkeypatch, tmp_path: Path
    ):
        """The loaded project config's own resolved ``machine`` (its tiered
        machine-local > global > detected lookup) must win over a fresh
        ``detect_machine(repo_dir)`` -- redetecting can disagree with it
        (e.g. a configured canonical alias, or no machines.yaml at all), and
        a later session's presence check compares against ``config.machine``."""
        from agent_worktrees import config as cfg

        configured_config = SimpleNamespace(machine="configured-alias")
        monkeypatch.setattr(cfg, "load_project_config", lambda _name: configured_config)
        monkeypatch.setattr(
            cfg, "detect_machine",
            lambda *_a, **_k: (_ for _ in ()).throw(
                AssertionError("detect_machine must not be called when config.machine is set")
            ),
        )

        recorded: dict[str, object] = {}
        monkeypatch.setattr(
            related_machine_presence,
            "record_local_presence",
            lambda config, repo_name, machine, *, cwd=None: recorded.update(
                machine=machine,
            )
            or True,
        )

        changed = related_machine_presence.record_on_adoption(
            "myproj", str(tmp_path),
        )

        assert changed is True
        assert recorded == {"machine": "configured-alias"}

    def test_never_raises_on_internal_failure(self, monkeypatch, tmp_path: Path):
        monkeypatch.setattr(
            related_machine_presence,
            "record_local_presence",
            lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        changed = related_machine_presence.record_on_adoption(
            "myproj", str(tmp_path),
        )

        assert changed is False
