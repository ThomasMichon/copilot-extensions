"""Ordinary construction never substitutes missing independent admission."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from agent_dispatch import assignment_admission as module
from agent_dispatch import assignment_relationship_owner, server
from agent_dispatch.assignment_workspace import AssignmentWorkspace
from agent_dispatch.config import Config, load_config
from agent_dispatch.queue_records import TaskError
from agent_dispatch.source_start import behavior_identity, config_identity


@pytest.mark.parametrize(("spec", "authority"), [
    ("", "local"), (None, "local"), ("policy:factory", None),
    ("policy:factory", "legacy"), ("policy:factory:other", "local"),
    ("policy..module:factory", "local"), ("policy:factory()", "local"),
])
def test_invalid_selection_refuses_before_import(monkeypatch, spec, authority):
    def forbidden(*args):
        pytest.fail("invalid admission configuration imported executable policy")

    monkeypatch.setattr(module.importlib, "import_module", forbidden)
    with pytest.raises(ValueError, match="relationship admission"):
        module.configured_writer(spec, authority)


def test_absent_selection_does_not_import_or_select_writer(monkeypatch):
    def forbidden(*args):
        pytest.fail("ordinary construction selected an admission provider")

    monkeypatch.setattr(module.importlib, "import_module", forbidden)
    assert module.configured_writer(None, None) is None


@pytest.mark.parametrize("failure", ["import", "factory", "shape", "missing-owner"])
def test_explicit_provider_failure_cannot_degrade_to_legacy(monkeypatch, failure):
    policy = ModuleType("policy")

    def factory():
        if failure == "factory":
            raise RuntimeError("cohort owner unavailable")
        if failure == "missing-owner":
            return module.RelationshipAdmission(None, None)
        return True

    policy.factory = factory

    def load(name):
        assert name == "policy"
        if failure == "import":
            raise ImportError("policy is unavailable")
        return policy

    monkeypatch.setattr(module.importlib, "import_module", load)
    with pytest.raises((ValueError, RuntimeError, ImportError)):
        module.configured_writer("policy:factory", "local")


def test_ordinary_app_retains_actual_independent_owners_and_queue_authority(monkeypatch, tmp_path):
    workspace = AssignmentWorkspace("host", "project", "worktree", "/workspace", "original")
    known_request = {"task_id": "original-task"}
    observations = []

    def admit_workspace(observed, identity):
        if observed != workspace:
            raise TaskError("independent workspace changed")
        observations.append(identity)

    def admit_request(observed, identity, request):
        admit_workspace(observed, identity)
        if request != known_request:
            raise TaskError("independent original request changed")

    admission = module.RelationshipAdmission(admit_workspace, admit_request)
    policy = ModuleType("policy")
    policy.factory = lambda: admission
    monkeypatch.setitem(sys.modules, "policy", policy)
    retained = SimpleNamespace(admit_workspace=None, admit_request=None)

    def selected(**kwargs):
        retained.admit_workspace = kwargs["admit_workspace"]
        retained.admit_request = kwargs["admit_request"]
        return retained

    monkeypatch.setattr(
        assignment_relationship_owner.WorktreeRelationshipWriter, "selected", selected,
    )
    monkeypatch.setattr(server.telemetry, "load_sink_from_config", lambda: True)
    queues = []

    def create_app(queue, **kwargs):
        queues.append(queue)
        return SimpleNamespace(state=SimpleNamespace())

    monkeypatch.setattr(server, "create_app", create_app)
    cfg = Config(
        db_path=str(tmp_path / "tasks.db"),
        relationship_admission="policy:factory", relationship_authority="local",
    )
    app = server.build_app(cfg)
    assert queues[0]._relationship_writer is retained
    assert queues[0]._relationship_authority == "local"
    assert retained.admit_workspace is admit_workspace
    assert retained.admit_request is admit_request
    retained.admit_request(workspace, {"runtime": "observed"}, known_request)
    with pytest.raises(TaskError, match="original request changed"):
        retained.admit_request(workspace, {}, {"task_id": "replacement"})
    assert observations[0] == {"runtime": "observed"}
    assert app.state.runtime_config_identity == config_identity(cfg)
    assert app.state.runtime_behavior_identity_v1 == behavior_identity(cfg)
    server.build_app(replace(cfg, relationship_admission=None, relationship_authority=None))
    assert queues[1]._relationship_writer is None
    assert queues[1]._relationship_authority is None


def test_config_reads_are_passive_and_source_reuse_distinguishes_admission(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_DISPATCH_RELATIONSHIP_ADMISSION", "missing_policy:factory")
    monkeypatch.setenv("AGENT_DISPATCH_RELATIONSHIP_AUTHORITY", "central")
    cfg = replace(load_config(), db_path=str(tmp_path / "tasks.db"))
    assert cfg.relationship_admission == "missing_policy:factory"
    assert cfg.relationship_authority == "central"
    identities = {
        config_identity(cfg),
        config_identity(replace(cfg, relationship_admission="other_policy:factory")),
        config_identity(replace(cfg, relationship_authority="local")),
        config_identity(replace(cfg, relationship_admission=None, relationship_authority=None)),
    }
    assert len(identities) == 4


def test_standing_policy_is_optional_and_retained_from_the_configured_owner(monkeypatch, tmp_path):
    standing = module.StandingAdmission(
        "host", tmp_path / "overrides.json", (sys.executable, "selected-owner"),
        lambda reference: tmp_path / "materialization.json",
    )
    policy = ModuleType("policy")
    admission = module.RelationshipAdmission(lambda *_: None, lambda *_: None, standing)
    policy.factory = lambda: admission
    monkeypatch.setitem(sys.modules, "policy", policy)
    selections = []
    monkeypatch.setattr(
        assignment_relationship_owner.WorktreeRelationshipWriter, "selected",
        lambda **kwargs: selections.append(kwargs) or kwargs,
    )
    assert module.configured_writer("policy:factory", "local")["standing"] is standing
    admission = replace(admission, standing=None)
    assert module.configured_writer("policy:factory", "local")["standing"] is None
    with pytest.raises(ValueError, match="configured owner"):
        replace(admission, standing={"machine": "host"})
    with pytest.raises(ValueError, match="absolute policy"):
        replace(standing, overrides_path=Path("relative-policy.json"))
    with pytest.raises(ValueError, match="selected owner"):
        replace(standing, materialization_command=("implicit-path-command",))


@pytest.mark.parametrize("authority", [None, "local"])
def test_actual_serve_entrypoint_preserves_explicit_admission_failure(monkeypatch, authority):
    from agent_dispatch import coordinator_cli, logging_setup

    monkeypatch.setenv("AGENT_DISPATCH_RELATIONSHIP_ADMISSION", "missing_admission:factory")
    if authority is None:
        monkeypatch.delenv("AGENT_DISPATCH_RELATIONSHIP_AUTHORITY", raising=False)
    else:
        monkeypatch.setenv("AGENT_DISPATCH_RELATIONSHIP_AUTHORITY", authority)
    monkeypatch.setattr(logging_setup, "configure_file_logging", lambda *args: None)
    monkeypatch.setattr(coordinator_cli, "_reroot_serve_cwd", lambda: None)

    def serve(cfg, **kwargs):
        module.configured_writer(cfg.relationship_admission, cfg.relationship_authority)

    monkeypatch.setattr(server, "serve", serve)
    args = SimpleNamespace(token=None, port=None, db=None, host=None, passive=True, force=False)
    with pytest.raises(ValueError if authority is None else ModuleNotFoundError):
        coordinator_cli._cmd_serve(args)


def test_factory_failure_precedes_socket_and_route_publication(monkeypatch, tmp_path):
    from agent_dispatch import procutil

    monkeypatch.setattr(procutil, "relocate_off_payload", lambda: None)
    monkeypatch.setattr(server, "_clear_routing", lambda: None)
    monkeypatch.setattr(server, "clear_endpoint", lambda *args, **kwargs: None)

    def bind(*args):
        pytest.fail("unqualified coordinator reached socket/routing publication")

    monkeypatch.setattr(server, "_bind_listen_socket", bind)
    with pytest.raises(ModuleNotFoundError):
        server.serve(Config(
            db_path=str(tmp_path / "tasks.db"),
            relationship_admission="missing_admission:factory", relationship_authority="local",
        ), passive=True)
