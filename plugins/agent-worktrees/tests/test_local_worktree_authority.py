"""Retained local checkout authority survives optional topology drift."""

from __future__ import annotations

import json
import os
import sys
from argparse import Namespace
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from agent_worktrees import claims_cli, config as cfg, execution_spaces, git_ops
from agent_worktrees import health, retained_checkout, tracking, tracking_producer_write
from agent_worktrees.worktree_machine_identity import WorktreeMachineIdentity
from test_resolve_seed_delivery import _args, _stub_launch_plumbing


@pytest.fixture(scope="session")
def local_git_checkout(tmp_path_factory):
    root = tmp_path_factory.mktemp("local-authority")
    anchor = root / "anchor"
    anchor.mkdir()
    git_ops.git("init", cwd=anchor)
    git_ops.git("-c", "user.name=Example", "-c", "user.email=example@example.com",
                "commit", "--allow-empty", "-m", "fixture", cwd=anchor)
    checkout = root / "worktrees" / "old-host-worktree"
    git_ops.git("worktree", "add", "-b", "worktree/example", str(checkout), cwd=anchor)
    return anchor, checkout


@pytest.fixture
def local_worktree(tmp_path, monkeypatch, local_git_checkout):
    anchor, checkout = local_git_checkout
    project = tmp_path / "example"
    records = project / "worktrees"
    records.mkdir(parents=True)
    platform = cfg.detect_platform()
    data = {
        "worktree_id": checkout.name, "branch": "worktree/example",
        "worktree_path": str(checkout), "repo": "example",
        "machine": "old-host", "platform": platform, "status": "active",
        "machine_identity_version": 1, "environment": platform,
        "execution_machine": "old-execution", "creation_nonce": "retained-incarnation",
        "sessions": [], "resources": [
            {"kind": "codespace", "ref": "retained-resource", "state": "active"},
        ],
    }
    ledger = records / f"{checkout.name}.yaml"
    ledger.write_text(yaml.safe_dump(data), encoding="utf-8")
    config_path = project / "config.yaml"
    config_path.write_text(yaml.safe_dump({
        "repo_name": "example", "machine": "new-host",
        "repos": {"example": {"anchor": str(anchor), "worktree_root": str(checkout.parent)}},
    }), encoding="utf-8")
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: tmp_path / (name or "example"))
    monkeypatch.setattr(cfg, "tracking_dir", lambda name=None: cfg.project_dir(name) / "worktrees")
    monkeypatch.setattr(cfg, "global_config_path", lambda: tmp_path / "missing.yaml")
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "new-host")
    monkeypatch.setattr(cfg, "_ACTIVE_PROJECT", "example")
    monkeypatch.setattr(cfg, "_load_inrepo_config", lambda *a, **k: {})
    load_config = cfg.load_config

    def load(*_args, **_kwargs):
        return load_config(
            config_path, project="example", include_control_plane_related_pr=False,
            include_knowledge_preferences=False,
        )

    monkeypatch.setattr(cfg, "load_config", load)
    monkeypatch.setattr(cfg, "load_project_config", load)
    return SimpleNamespace(
        anchor=anchor, checkout=checkout, ledger=ledger, data=data,
        load=load, topology=anchor / "machines.yaml",
    )


def _topology(fixture, kind):
    if kind == "missing":
        fixture.topology.unlink(missing_ok=True)
    elif kind == "malformed":
        fixture.topology.write_text("machines: [\n", encoding="utf-8")
    else:
        fixture.topology.write_text(yaml.safe_dump({"machines": {
            "wrong-space": {
                "hostname": "new-host" if kind == "mis-mapped" else "another-host",
                "physical_host": "another-machine",
                "execution_platform": "wsl" if cfg.detect_platform() != "wsl" else "windows",
            },
        }}), encoding="utf-8")


@pytest.mark.parametrize("topology", ["missing", "unrelated", "mis-mapped", "malformed"])
@pytest.mark.parametrize("versioned", [False, True])
def test_local_owner_and_doctor_do_not_require_historical_machine_mapping(
    local_worktree, monkeypatch, topology, versioned,
):
    fixture = local_worktree
    _topology(fixture, topology)
    if not versioned:
        for field in ("machine_identity_version", "environment", "execution_machine"):
            fixture.data.pop(field)
        fixture.ledger.write_text(yaml.safe_dump(fixture.data), encoding="utf-8")
    config = fixture.load()
    record = tracking.load_record(fixture.ledger)
    before = fixture.ledger.read_bytes()

    def forbidden(*_args, **_kwargs):
        pytest.fail("retained local ownership consulted optional machine topology")

    monkeypatch.setattr(execution_spaces, "registry_for_config", forbidden)
    execution_spaces.require_record_mutation(record, config)
    owner = tracking.format_claim_ref(record.execution_machine, record.repo, record.worktree_id)
    assert execution_spaces.resolve_owner_record_path(owner, config) == (
        fixture.ledger, record.worktree_id, None,
    )
    assert tracking_producer_write._owned_record({"owner_ref": owner}, fixture.ledger)[0].worktree_id == record.worktree_id
    report = health.audit_local_registration([record])
    assert report["local"] == 1 and report["unproven"] == 0
    assert fixture.ledger.read_bytes() == before


@pytest.mark.parametrize("topology", ["mis-mapped", "malformed"])
@pytest.mark.parametrize("selector", [None, "old-execution", "new-host"])
def test_real_resolve_resume_and_original_owner_settlement(
    local_worktree, monkeypatch, capfd, topology, selector,
):
    from agent_worktrees import __main__ as cli
    from agent_worktrees import local_cache_refresh, push_timeout, resolve_cli

    fixture = local_worktree
    _topology(fixture, topology)
    config = fixture.load()
    record = tracking.load_record(fixture.ledger)
    original_identity = record.machine_identity
    original_resources = record.resources
    before_resume = record.resume_count
    _stub_launch_plumbing(monkeypatch, config)
    monkeypatch.setattr(cli, "_build_launch_cmd", lambda *a, **k: [
        sys.executable, "-c", "print('retained-local-backend-started')",
    ])
    monkeypatch.setattr(local_cache_refresh, "prepare_tool_roster", lambda *a, **k: None)
    monkeypatch.setattr(cfg, "load_project_config", lambda *a, **k: config)
    code = resolve_cli.cmd_resolve(_args(
        worktree_id=record.worktree_id, dry_run=False, no_resume=True,
        defer_projections=True, machine=selector,
    ))
    plan = json.loads(capfd.readouterr().out)
    assert code == 0, plan
    plan = plan.get("launch", plan)
    assert plan["action"] == "exec" and plan["work_dir"] == str(fixture.checkout)
    resumed = tracking.load_record(fixture.ledger)
    assert resumed.resume_count == before_resume + 1
    assert resumed.machine_identity == original_identity
    assert resumed.resources == original_resources
    launched = push_timeout.run_bounded(
        plan["cmd"], cwd=str(fixture.checkout), timeout=30,
        env={**os.environ, "PYTHONPATH": str(Path(resolve_cli.__file__).resolve().parents[1])},
    )
    assert launched.returncode == 0, launched.stderr
    assert launched.stdout.strip() == "retained-local-backend-started"
    owner = tracking.format_claim_ref(record.execution_machine, record.repo, record.worktree_id)
    assert claims_cli._claims_settle(Namespace(
        claim_owner_ref=owner, json=True, released=False,
    ), "retained-resource") == 0
    assert not json.loads(capfd.readouterr().out).get("deferred")
    settled = tracking.load_record(fixture.ledger)
    assert settled.resources[0].state == "at-rest"
    assert settled.machine_identity == original_identity


def test_local_proof_rejects_a_changed_snapshot_and_an_unrelated_owner(local_worktree):
    fixture = local_worktree
    _topology(fixture, "missing")
    config = fixture.load()
    record = tracking.load_record(fixture.ledger)
    record.machine_identity = WorktreeMachineIdentity("changed-host", record.platform, "changed-owner")
    assert not retained_checkout.is_local(record, config)
    ref = tracking.parse_claim_ref(f"foreign/example/{record.worktree_id}")
    assert retained_checkout.resolve_owner(ref, config) is None


def test_local_proof_requires_the_actual_receiving_git_family(local_worktree, tmp_path):
    fixture = local_worktree
    _topology(fixture, "missing")
    config = fixture.load()
    config = replace(config, repos={
        "example": replace(config.repos["example"], anchor=str(tmp_path / "unrelated-anchor")),
    })
    assert not retained_checkout.is_local(tracking.load_record(fixture.ledger), config)


def test_local_proof_is_filesystem_only(local_worktree, monkeypatch):
    fixture = local_worktree
    _topology(fixture, "missing")
    config = fixture.load()
    record = tracking.load_record(fixture.ledger)
    monkeypatch.setattr(
        git_ops, "git",
        lambda *a, **k: pytest.fail("local checkout authority spawned Git"),
    )
    assert retained_checkout.is_local(record, config)


def test_local_checkout_does_not_override_an_explicit_foreign_target(local_worktree):
    from agent_worktrees.resolve_target import retained_target_is_local

    fixture = local_worktree
    _topology(fixture, "missing")
    config = fixture.load()
    assert not retained_target_is_local(config, fixture.checkout.name, "foreign-host")
    other_environment = "wsl" if cfg.detect_platform() != "wsl" else "windows"
    assert not retained_target_is_local(
        config, fixture.checkout.name, "old-execution", other_environment,
    )
