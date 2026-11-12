"""Retained local checkout authority survives optional topology drift."""

from __future__ import annotations

import json
import hashlib
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


def test_registered_anchor_is_local_without_machine_topology(local_worktree):
    fixture = local_worktree
    _topology(fixture, "malformed")
    config = fixture.load()
    ledger = fixture.ledger.parent / "@anchor.yaml"
    ledger.write_text(yaml.safe_dump({
        **fixture.data, "worktree_id": "@anchor", "worktree_path": str(fixture.anchor),
        "kind": "anchor", "resources": [],
    }), encoding="utf-8")
    record = tracking.load_record(ledger)
    execution_spaces.require_record_mutation(record, config)
    assert retained_checkout.is_local(record, config)
    report = health.audit_local_registration([record, tracking.load_record(fixture.ledger)])
    assert report["local"] == 2 and report["unproven"] == 0


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


@pytest.mark.parametrize("versioned", [False, True])
def test_local_checkout_cannot_cross_the_actual_execution_platform(local_worktree, versioned):
    fixture = local_worktree
    _topology(fixture, "missing")
    other = "wsl" if cfg.detect_platform() != "wsl" else "windows"
    fixture.data["platform"] = other
    if versioned:
        fixture.data["environment"] = other
    else:
        for field in ("machine_identity_version", "environment", "execution_machine"):
            fixture.data.pop(field)
    fixture.ledger.write_text(yaml.safe_dump(fixture.data), encoding="utf-8")
    record = tracking.load_record(fixture.ledger)
    config = fixture.load()
    assert not retained_checkout.is_local(record, config)
    with pytest.raises(execution_spaces.ExecutionSpaceError, match="platform"):
        execution_spaces.require_record_mutation(record, config)


@pytest.mark.parametrize("ambiguous_suffix", [False, True])
def test_declared_remote_retains_selector_despite_retained_local_record(
    local_worktree, monkeypatch, capfd, ambiguous_suffix,
):
    from agent_worktrees import installer, resolve_cli

    fixture = local_worktree
    _topology(fixture, "missing")
    selector = "old-execution"
    raw_id = fixture.checkout.name
    if ambiguous_suffix:
        other = fixture.ledger.with_name("another-worktree.yaml")
        other.write_text(yaml.safe_dump({
            **fixture.data, "worktree_id": other.stem,
        }), encoding="utf-8")
        raw_id = "worktree"
    entry = cfg.MachineEntry(
        key=selector, display_name="Remote", hostname="remote-host",
        execution_platform=cfg.detect_platform(),
    )
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda *a, **k: {selector: entry})
    monkeypatch.setattr(installer, "read_projects_registry", lambda: {"projects": {"example": {}}})
    routed = []
    monkeypatch.setattr(
        resolve_cli, "_emit_remote_plan_for_env",
        lambda *args, **kwargs: routed.append(args) or 0,
    )
    before = fixture.ledger.read_bytes()
    assert resolve_cli.cmd_resolve(_args(worktree_id=raw_id, machine=selector)) == 0
    assert routed[0][1] == selector
    assert routed[0][3][:2] == ["--worktree-id", raw_id]
    assert cfg.active_project() == "example"
    assert fixture.ledger.read_bytes() == before
    capfd.readouterr()


def test_ambiguous_declared_target_cannot_fall_back_to_a_historical_alias(
    local_worktree, monkeypatch,
):
    from agent_worktrees import resolve_target

    fixture = local_worktree
    _topology(fixture, "missing")
    config = fixture.load()
    entries = {name: cfg.MachineEntry(
        key=name, display_name=name, alias="old-execution", hostname=name,
        execution_platform=cfg.detect_platform(),
    ) for name in ("remote-one", "remote-two")}
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda *a, **k: entries)
    with pytest.raises(ValueError, match="[Aa]mbiguous"):
        resolve_target.target_is_local(
            config, "old-execution", worktree_id=fixture.checkout.name,
        )


def test_cross_project_local_probe_uses_receiving_config_without_rebinding_remote(
    local_worktree, monkeypatch, capfd,
):
    from agent_worktrees import installer, local_cache_refresh, resolve_cli, resolve_target

    fixture = local_worktree
    _topology(fixture, "malformed")
    receiving = fixture.load()
    ambient = replace(receiving, repo_name="other", machine="other-host", repos={
        "other": receiving.default_repo,
    })
    _stub_launch_plumbing(monkeypatch, receiving)
    monkeypatch.setattr(cfg, "_ACTIVE_PROJECT", "other")
    monkeypatch.setattr(cfg, "tracking_dir", lambda name=None: cfg.project_dir(
        name or cfg.active_project(),
    ) / "worktrees")
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: (
        receiving if cfg.active_project() == receiving.repo_name else ambient
    ))
    monkeypatch.setattr(installer, "read_projects_registry", lambda: {
        "projects": {"example": {}, "other": {}},
    })
    assert resolve_target.retained_target_is_local(ambient, fixture.checkout.name, "old-execution")
    assert cfg.active_project() == "other"
    assert not resolve_target.retained_target_is_local(ambient, fixture.checkout.name, "foreign-host")
    assert cfg.active_project() == "other"
    monkeypatch.setattr(local_cache_refresh, "prepare_tool_roster", lambda *a, **k: None)
    assert resolve_cli.cmd_resolve(_args(
        worktree_id=fixture.checkout.name, machine="old-execution",
        no_resume=True, defer_projections=True,
    )) == 0
    plan = json.loads(capfd.readouterr().out)
    assert plan.get("launch", plan)["work_dir"] == str(fixture.checkout)
    assert cfg.active_project() == "example"


def test_journal_recovered_receipt_keeps_readonly_local_proof(local_worktree, monkeypatch):
    from agent_worktrees import creation_requests, receiving_allocation, worktree_trust

    fixture = local_worktree
    _topology(fixture, "malformed")
    config = fixture.load()
    admission = receiving_allocation.ReceivingAllocation.from_fields({
        "origin": "remote/parent/origin", "request_id": "original", "token": "a" * 32,
        "target": {"machine": "old-execution", "project": "example", "options": {"no_pair": True}},
    })
    fixture.data["owner_ref"] = admission.origin
    fixture.ledger.write_text(yaml.safe_dump(fixture.data), encoding="utf-8")
    record = tracking.load_record(fixture.ledger)
    child = {
        "id": record.worktree_id, "repo": record.repo,
        "branch": record.branch, "path": record.worktree_path,
    }
    connection = creation_requests._connection(fixture.ledger.parent.parent / "workspace-creation.sqlite3")
    with connection:
        connection.execute("INSERT INTO creations VALUES (?, 1, ?, 'complete', ?)", (
            hashlib.sha256(admission.key.encode()).hexdigest(),
            json.dumps({"receiving_allocation": admission.to_fields()}),
            json.dumps({"creation_nonce": record.creation_nonce, "worktree": child}),
        ))
    connection.close()
    before = fixture.ledger.read_bytes()
    monkeypatch.setattr(
        execution_spaces, "registry_for_config",
        lambda *a: pytest.fail("recovered local receipt consulted optional topology"),
    )
    execution_spaces.require_record_mutation(record, config)
    assert record.receiving_allocation_present
    monkeypatch.setattr(
        creation_requests, "_connection",
        lambda *a: pytest.fail("local proof opened a journal schema writer"),
    )
    assert retained_checkout.is_local(record, config)
    trusted = []
    monkeypatch.setattr(
        worktree_trust.permissions, "ensure_trusted_folder",
        lambda path: trusted.append(path) or True,
    )
    assert worktree_trust.ensure_record_trusted(record, config)
    assert trusted == [record.worktree_path]
    owner = tracking.format_claim_ref(record.execution_machine, record.repo, record.worktree_id)
    assert tracking_producer_write._owned_record({"owner_ref": owner}, fixture.ledger)[0].receiving_allocation_present
    assert fixture.ledger.read_bytes() == before
    record.receiving_allocation["token"] = "b" * 32
    assert not retained_checkout.is_local(record, config)
