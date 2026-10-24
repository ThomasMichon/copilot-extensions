from __future__ import annotations

import pytest

from worktree_manager import engine_client
from worktree_manager.production_picker import context, engine_group_b


def test_project_context_remains_unbound_outside_picker_ui_fixture() -> None:
    assert context.project_bootstrap() is None
    with pytest.raises(RuntimeError, match="not bound"):
        context.project()


def test_picker_bootstrap_reads_group_b_contract(monkeypatch):
    payload = {
        "version": 1,
        "project": "dotfiles",
        "should_switch_cwd": True,
        "cwd": "/state/dotfiles",
        "default_live": False,
    }
    calls = []
    monkeypatch.setattr(
        engine_client, "run_json",
        lambda project, args, **kwargs: calls.append((project, args)) or payload,
    )

    assert engine_group_b.picker_bootstrap("dotfiles") == payload
    assert calls == [("dotfiles", ["picker-bootstrap", "--json", "--project-config"])]


def test_picker_bootstrap_older_engine_compatibility(monkeypatch):
    calls = []
    timeouts = []
    clock = iter([10.0, 17.0])
    monkeypatch.setattr(engine_group_b, "monotonic", lambda: next(clock))
    payload = {"project": "example", "project_config": {"config_dir": "/state"}}

    def read(project, args, **kwargs):
        calls.append(args)
        timeouts.append(kwargs["timeout"])
        if "--project-config" in args:
            raise engine_client.EngineError(
                "engine failed (exit 2): usage: engine picker-bootstrap\n"
                "engine: error: unrecognized arguments: --project-config"
            )
        return payload

    monkeypatch.setattr(engine_client, "run_json", read)
    assert engine_group_b.picker_bootstrap("example", timeout=30) == payload
    assert calls == [
        ["picker-bootstrap", "--json", "--project-config"], ["picker-bootstrap", "--json"],
    ]
    assert timeouts == [30, 23]


def test_picker_bootstrap_compatibility_cannot_extend_deadline(monkeypatch):
    clock = iter([10.0, 40.0])
    monkeypatch.setattr(engine_group_b, "monotonic", lambda: next(clock))
    calls = []

    def read(project, args, **kwargs):
        calls.append(args)
        raise engine_client.EngineError("unrecognized arguments: --project-config")

    monkeypatch.setattr(engine_client, "run_json", read)
    with pytest.raises(engine_client.EngineError, match="unrecognized arguments"):
        engine_group_b.picker_bootstrap("example", timeout=30)
    assert len(calls) == 1


@pytest.mark.parametrize("detail", [
    "configuration refused", "timed out", "unrecognized arguments: --other",
])
def test_picker_bootstrap_never_retries_operational_failures(monkeypatch, detail):
    calls = []

    def read(project, args, **kwargs):
        calls.append(args)
        raise engine_client.EngineError(detail)

    monkeypatch.setattr(engine_client, "run_json", read)
    with pytest.raises(engine_client.EngineError, match=detail):
        engine_group_b.picker_bootstrap("example")
    assert len(calls) == 1


def test_picker_bootstrap_classifies_unsupported_engine(monkeypatch):
    def _boom(*args, **kwargs):
        raise engine_client.EngineError(
            "agent-worktrees picker-bootstrap --json failed "
            "(exit 2): invalid choice: 'picker-bootstrap'"
        )

    monkeypatch.setattr(engine_client, "run_json", _boom)

    with pytest.raises(engine_client.EngineFeatureUnavailable):
        engine_group_b.picker_bootstrap("dotfiles")


def test_repair_stale_anchor_reads_group_b_contract(monkeypatch):
    payload = {
        "version": 1,
        "project": "dotfiles",
        "status": "unchanged",
        "self_present_before": True,
        "self_present_after": True,
    }
    monkeypatch.setattr(engine_client, "run_json", lambda *args, **kwargs: payload)

    assert engine_group_b.repair_stale_anchor("dotfiles") == payload


def test_repair_stale_anchor_classifies_unsupported_engine(monkeypatch):
    def _boom(*args, **kwargs):
        raise engine_client.EngineError(
            "agent-worktrees repair-stale-anchor --json failed "
            "(exit 2): unknown command repair-stale-anchor"
        )

    monkeypatch.setattr(engine_client, "run_json", _boom)

    with pytest.raises(engine_client.EngineFeatureUnavailable):
        engine_group_b.repair_stale_anchor("dotfiles")


def test_bind_project_bootstrap_records_authoritative_project(tmp_path):
    context.set_project("requested-project")

    binding = context.bind_project_bootstrap(
        {
            "version": 1,
            "project": "resolved-project",
            "should_switch_cwd": True,
            "cwd": "/state/resolved-project",
            "default_live": True,
            "workspace": str(tmp_path / "tracked-workspace"),
        }
    )

    assert context.project() == "resolved-project"
    assert context.project_bootstrap() == binding
    assert binding.cwd == "/state/resolved-project"
    assert binding.should_switch_cwd is True
    assert binding.default_live is True
    assert binding.workspace == str(tmp_path / "tracked-workspace")


@pytest.mark.parametrize("workspace", ["relative", "", [], {}])
def test_bind_project_bootstrap_rejects_invalid_workspace(workspace):
    with pytest.raises(ValueError, match="absolute tracked path"):
        context.bind_project_bootstrap({
            "project": "sample", "workspace": workspace,
        })


def test_set_project_clears_stale_bootstrap_binding():
    context.bind_project_bootstrap(
        {
            "version": 1,
            "project": "resolved-project",
            "should_switch_cwd": False,
            "cwd": None,
            "default_live": False,
        }
    )

    context.set_project("next-project")

    assert context.project() == "next-project"
    assert context.project_bootstrap() is None
