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
    elif kind == "invalid-types":
        fixture.topology.write_text(yaml.safe_dump({"machines": {
            "new-host": {"ssh": {"environments": 1}},
        }}), encoding="utf-8")
    else:
        fixture.topology.write_text(yaml.safe_dump({"machines": {
            "wrong-space": {
                "hostname": "new-host" if kind == "mis-mapped" else "another-host",
                "physical_host": "another-machine",
                "execution_platform": "wsl" if cfg.detect_platform() != "wsl" else "windows",
            },
        }}), encoding="utf-8")


@pytest.mark.parametrize("topology", ["missing", "unrelated", "mis-mapped", "malformed", "invalid-types"])
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


@pytest.mark.parametrize("topology", ["mis-mapped", "malformed", "invalid-types"])
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


@pytest.mark.parametrize("exact_suffix_collision", [False, True])
@pytest.mark.parametrize("declared_local", [False, True])
def test_cross_project_local_probe_uses_receiving_config_without_rebinding_remote(
    local_worktree, monkeypatch, capfd, tmp_path, exact_suffix_collision, declared_local,
):
    from agent_worktrees import installer, local_cache_refresh, resolve_cli, resolve_target

    fixture = local_worktree
    _topology(fixture, "malformed")
    if exact_suffix_collision:
        original_checkout = fixture.checkout
        fixture.checkout = tmp_path / "exact" / "worktree"
        branch = f"worktree/exact-{declared_local}"
        git_ops.git(
            "worktree", "add", "-b", branch, str(fixture.checkout), cwd=fixture.anchor,
        )
        fixture.ledger.unlink()
        fixture.data.update(
            worktree_id="worktree", branch=branch, worktree_path=str(fixture.checkout),
        )
        fixture.ledger = fixture.ledger.with_name("worktree.yaml")
        fixture.ledger.write_text(yaml.safe_dump(fixture.data), encoding="utf-8")
        ambient_records = cfg.project_dir("other") / "worktrees"
        ambient_records.mkdir(parents=True)
        (ambient_records / "active-worktree.yaml").write_text(yaml.safe_dump({
            **fixture.data, "worktree_id": "active-worktree", "repo": "other",
            "worktree_path": str(original_checkout), "branch": "worktree/example",
            "machine": "other-host", "execution_machine": "other-execution",
        }), encoding="utf-8")
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
    if declared_local:
        entry = cfg.MachineEntry(
            key=ambient.machine, display_name="Current", hostname="new-host",
            execution_platform=cfg.detect_platform(),
        )
        monkeypatch.setattr(cfg, "load_machines_yaml", lambda anchor, *, config=None: (
            {entry.key: entry} if config is not None and config.repo_name == "other" else {}
        ))
    assert resolve_target.retained_target_is_local(ambient, fixture.checkout.name, "old-execution")
    assert cfg.active_project() == "other"
    assert not resolve_target.retained_target_is_local(ambient, fixture.checkout.name, "foreign-host")
    assert cfg.active_project() == "other"
    monkeypatch.setattr(local_cache_refresh, "prepare_tool_roster", lambda *a, **k: None)
    assert resolve_cli.cmd_resolve(_args(
        worktree_id=fixture.checkout.name, machine=ambient.machine if declared_local else "old-execution",
        no_resume=True, defer_projections=True,
    )) == 0
    plan = json.loads(capfd.readouterr().out)
    assert plan.get("launch", plan)["work_dir"] == str(fixture.checkout)
    assert cfg.active_project() == "example"


def _receiving_child(fixture):
    from agent_worktrees import creation_requests, receiving_allocation

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
    return record


def test_journal_recovered_receipt_keeps_readonly_local_proof(local_worktree, monkeypatch):
    from agent_worktrees import creation_requests, worktree_trust

    fixture = local_worktree
    _topology(fixture, "malformed")
    config = fixture.load()
    record = _receiving_child(fixture)
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


@pytest.mark.parametrize("journal", ["missing", "conflicting"])
def test_failed_receipt_recovery_cannot_authorize_foreign_parent(local_worktree, journal):
    fixture = local_worktree
    _topology(fixture, "malformed")
    config = fixture.load()
    if journal == "conflicting":
        _receiving_child(fixture)
    fixture.data.update(owner_ref="remote/parent/origin", creation_nonce="different-incarnation")
    fixture.ledger.write_text(yaml.safe_dump(fixture.data), encoding="utf-8")
    before = fixture.ledger.read_bytes()
    record = tracking.load_record(fixture.ledger)
    assert retained_checkout.is_local(record, config)
    with pytest.raises(execution_spaces.ExecutionSpaceError, match="receiving allocation"):
        execution_spaces.require_record_mutation(record, config)
    assert not record.receiving_allocation_present
    assert fixture.ledger.read_bytes() == before


def test_actual_retained_local_parent_needs_no_foreign_receipt(local_worktree, tmp_path):
    fixture = local_worktree
    _topology(fixture, "malformed")
    config = fixture.load()
    parent = tmp_path / "parent"
    git_ops.git("worktree", "add", "-b", "worktree/parent", str(parent), cwd=fixture.anchor)
    (fixture.ledger.parent / "parent.yaml").write_text(yaml.safe_dump({
        **fixture.data, "worktree_id": "parent", "worktree_path": str(parent),
        "branch": "worktree/parent", "creation_nonce": "parent-incarnation",
    }), encoding="utf-8")
    fixture.data["owner_ref"] = "old-execution/example/parent"
    fixture.ledger.write_text(yaml.safe_dump(fixture.data), encoding="utf-8")
    execution_spaces.require_record_mutation(tracking.load_record(fixture.ledger), config)


@pytest.mark.parametrize("name_kind", ["key", "alias", "ambiguous-alias", "display", "ambiguous-display"])
def test_explicit_remote_name_equal_to_observed_hostname_is_not_local(
    local_worktree, monkeypatch, capfd, name_kind,
):
    from agent_worktrees import installer, resolve_cli

    fixture = local_worktree
    _topology(fixture, "missing")
    selector = cfg.socket.gethostname()
    keys = [selector] if name_kind == "key" else ["remote-one"]
    if name_kind.startswith("ambiguous"):
        keys.append("remote-two")
    entries = {key: cfg.MachineEntry(
        key=key, display_name=selector if "display" in name_kind else key,
        alias=selector if "alias" in name_kind else "",
        hostname=f"{key}-remote",
        execution_platform="" if "display" in name_kind else cfg.detect_platform(),
    ) for key in keys}
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda *a, **k: entries)
    monkeypatch.setattr(installer, "read_projects_registry", lambda: {"projects": {"example": {}}})
    routed = []
    monkeypatch.setattr(
        resolve_cli, "_emit_remote_plan_for_env",
        lambda *args, **kwargs: routed.append(args) or 0,
    )
    before = fixture.ledger.read_bytes()
    code = resolve_cli.cmd_resolve(_args(worktree_id=fixture.checkout.name, machine=selector))
    if name_kind.startswith("ambiguous"):
        assert code == 1 and not routed
        message = json.loads(capfd.readouterr().out)["error"].lower()
        assert "ambiguous" in message or "multiple machines" in message
    else:
        assert code == 0 and routed[0][1] == selector
    assert fixture.ledger.read_bytes() == before


def test_replacement_checkout_branch_cannot_authorize_forced_cleanup(
    local_worktree, monkeypatch, tmp_path,
):
    from agent_worktrees import cleanup_gc_cli

    fixture = local_worktree
    _topology(fixture, "missing")
    replacement = tmp_path / "old-root" / fixture.checkout.name
    git_ops.git("worktree", "add", "-b", "replacement", str(replacement), cwd=fixture.anchor)
    changed = replacement / "uncommitted.txt"
    changed.write_text("preserve replacement work", encoding="utf-8")
    fixture.data["worktree_path"] = str(replacement)
    fixture.ledger.write_text(yaml.safe_dump(fixture.data), encoding="utf-8")
    config = fixture.load()
    config = replace(config, repos={"example": replace(
        config.default_repo, worktree_root=str(tmp_path / "new-root"),
    )})
    monkeypatch.setattr(cfg, "load_project_config", lambda *a, **k: config)
    record = tracking.load_record(fixture.ledger)
    assert retained_checkout.is_local(record, config)
    assert retained_checkout.recorded_branch_matches(record, config) is False
    result = cleanup_gc_cli._revalidate_cleanup_safety(
        record.worktree_id, repo=config.default_repo, tracking_path=fixture.ledger.parent, force=True,
        reap=lambda *args: pytest.fail("replacement checkout reached destructive cleanup"),
    )
    assert not result.cleanable and result.bucket == "execution-space"
    assert changed.read_text(encoding="utf-8") == "preserve replacement work"


def test_caller_project_remote_declaration_precedes_receiving_history(local_worktree, monkeypatch):
    from agent_worktrees import installer, resolve_cli

    fixture = local_worktree
    _topology(fixture, "missing")
    receiving = fixture.load()
    caller = replace(receiving, repo_name="other", machine="other-host", repos={
        "other": receiving.default_repo,
    })
    entry = cfg.MachineEntry(
        key="old-execution", display_name="Remote", hostname="remote-host",
        execution_platform=cfg.detect_platform(),
    )
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda anchor, *, config=None: (
        {entry.key: entry} if config is not None and config.repo_name == "other" else {}
    ))
    monkeypatch.setattr(cfg, "_ACTIVE_PROJECT", "other")
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: caller)
    monkeypatch.setattr(cfg, "tracking_dir", lambda name=None: cfg.project_dir(
        name or cfg.active_project(),
    ) / "worktrees")
    monkeypatch.setattr(installer, "read_projects_registry", lambda: {
        "projects": {"example": {}, "other": {}},
    })
    monkeypatch.setattr(
        resolve_cli, "_preflight_launch",
        lambda *a, **k: pytest.fail("caller-selected remote action reached a local checkout"),
    )
    routed = []
    monkeypatch.setattr(
        resolve_cli, "_emit_remote_plan_for_env",
        lambda *args, **kwargs: routed.append(args) or 0,
    )
    assert resolve_cli.cmd_resolve(_args(
        worktree_id=fixture.checkout.name, machine=entry.key,
    )) == 0
    assert routed[0][0] is caller and routed[0][1] == entry.key
    assert cfg.active_project() == "other"


@pytest.mark.parametrize("missing", ["branch", "worktree_id"])
def test_malformed_local_record_does_not_consume_remote_selector(local_worktree, monkeypatch, missing):
    from agent_worktrees import resolve_cli

    fixture = local_worktree
    _topology(fixture, "missing")
    fixture.data.pop(missing)
    fixture.ledger.write_text(yaml.safe_dump(fixture.data), encoding="utf-8")
    entry = cfg.MachineEntry(
        key="remote-target", display_name="Remote", hostname="remote-host",
        execution_platform=cfg.detect_platform(),
    )
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda *a, **k: {entry.key: entry})
    routed = []
    monkeypatch.setattr(
        resolve_cli, "_emit_remote_plan_for_env",
        lambda *args, **kwargs: routed.append(args) or 0,
    )
    before = fixture.ledger.read_bytes()
    assert resolve_cli.cmd_resolve(_args(
        worktree_id=fixture.checkout.name, machine=entry.key,
    )) == 0
    assert routed[0][3][:2] == ["--worktree-id", fixture.checkout.name]
    assert fixture.ledger.read_bytes() == before


@pytest.mark.parametrize("kind", ["separate", "linked", "copied-linked"])
def test_git_file_anchor_retains_local_authority(local_worktree, monkeypatch, tmp_path, kind):
    fixture = local_worktree
    _topology(fixture, "malformed")
    anchor = tmp_path / kind / "anchor"
    if kind == "separate":
        anchor.mkdir(parents=True)
        git_ops.git("init", "--separate-git-dir", str(anchor.parent / "metadata"), cwd=anchor)
        git_ops.git(
            "-c", "user.name=Example", "-c", "user.email=example@example.com",
            "commit", "--allow-empty", "-m", "fixture", cwd=anchor,
        )
    else:
        git_ops.git(
            "worktree", "add", "-b", f"worktree/{kind}-anchor", str(anchor), cwd=fixture.anchor,
        )
        if kind == "copied-linked":
            copied = anchor.parent / "copied"
            copied.mkdir()
            (copied / ".git").write_text((anchor / ".git").read_text(encoding="utf-8"), encoding="utf-8")
            anchor = copied
    checkout = anchor.parent / fixture.checkout.name
    branch = f"worktree/git-file-{kind}"
    git_ops.git("worktree", "add", "-b", branch, str(checkout), cwd=anchor)
    fixture.data.update(worktree_path=str(checkout), branch=branch)
    fixture.ledger.write_text(yaml.safe_dump(fixture.data), encoding="utf-8")
    config = fixture.load()
    config = replace(config, repos={"example": replace(
        config.default_repo, anchor=str(anchor), worktree_root=str(checkout.parent),
    )})
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(cfg, "load_project_config", lambda *a, **k: config)
    monkeypatch.setattr(git_ops, "git", lambda *a, **k: pytest.fail("local proof spawned Git"))
    record = tracking.load_record(fixture.ledger)
    if kind == "copied-linked":
        assert not retained_checkout.is_local(record, config)
        assert health.audit_local_registration([record])["unproven"] == 1
        return
    execution_spaces.require_record_mutation(record, config)
    assert retained_checkout.is_local(record, config)
    assert health.audit_local_registration([record])["local"] == 1


@pytest.mark.parametrize("qualified", [False, True])
def test_rejected_configured_machine_cannot_confirm_claimant_death(
    local_worktree, monkeypatch, qualified,
):
    from agent_worktrees import claimant, prune

    fixture = local_worktree
    other = "wsl" if cfg.detect_platform() != "wsl" else "windows"
    fixture.topology.write_text(yaml.safe_dump({"machines": {
        "new-host": {"execution_platform": other},
    }}), encoding="utf-8")
    config = fixture.load()
    assert config.machine == "new-host"
    ref = "new-host/example/missing-owner" if qualified else "missing-owner"
    monkeypatch.setattr(
        claimant, "_remote_claimant_alive",
        lambda *a, **k: pytest.fail("invalid local execution identity triggered a remote probe"),
    )
    assert claimant.local_claimant_alive(ref) is None
    assert claimant.resolve_claimant_alive(ref) is None
    child = replace(tracking.load_record(fixture.ledger), owner_ref=ref, resources=[])
    verdict = prune.assess(
        child, git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.UNUSED),
        turn_count=0, claimant_alive=claimant.local_claimant_alive,
    )
    assert not verdict.safe and verdict.category == "claimed"
    assert "unconfirmed" in verdict.reason


def test_proven_current_context_can_confirm_a_missing_local_claimant(local_worktree):
    from agent_worktrees import claimant

    _topology(local_worktree, "missing")
    assert claimant.local_claimant_alive("new-host/example/missing-owner") is False
    assert claimant.resolve_claimant_alive("new-host/example/missing-owner") is False


def test_retained_local_claimant_survives_invalid_topology(local_worktree):
    from agent_worktrees import claimant

    fixture = local_worktree
    _topology(fixture, "malformed")
    ref = f"old-execution/example/{fixture.checkout.name}"
    assert claimant.local_claimant_alive(ref) is True
    assert claimant.resolve_claimant_alive(ref, allow_remote=False) is True


@pytest.mark.parametrize("topology", ["scoped", "absent"])
@pytest.mark.parametrize("status", ["active", "finalized"])
def test_native_owner_cannot_select_coincident_versioned_guest(
    local_worktree, monkeypatch, capfd, topology, status,
):
    from agent_worktrees import claimant, prune

    fixture = local_worktree
    execution = "guest-space" if topology == "scoped" else "desk-wsl"
    if topology == "scoped":
        fixture.topology.write_text(yaml.safe_dump({"machines": {
            "desk": {"execution_platform": "windows", "physical_host": "desk"},
            execution: {"execution_platform": "wsl", "physical_host": "desk"},
        }}), encoding="utf-8")
    else:
        fixture.topology.unlink(missing_ok=True)
    monkeypatch.setattr(cfg, "detect_platform", lambda: "wsl")
    fixture.data.update(
        machine="desk", platform="wsl", environment="wsl",
        execution_machine=execution, status=status,
    )
    fixture.ledger.write_text(yaml.safe_dump(fixture.data), encoding="utf-8")
    machine = execution if topology == "scoped" else "desk"
    config = replace(
        fixture.load(), machine=machine, platform="wsl", configured_machine=machine,
    )
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(cfg, "load_project_config", lambda *a, **k: config)
    native_ref = f"desk/example/{fixture.checkout.name}"
    guest_ref = f"{execution}/example/{fixture.checkout.name}"
    native = tracking.parse_claim_ref(native_ref)
    assert retained_checkout.resolve_owner(native, config) is None
    assert execution_spaces.resolve_owner_record_path(native_ref, config)[0] is None
    assert retained_checkout.resolve_owner(tracking.parse_claim_ref(guest_ref), config) == fixture.ledger
    before = fixture.ledger.read_bytes()
    assert claims_cli._claims_settle(Namespace(
        claim_owner_ref=native_ref, json=True, released=False,
    ), "retained-resource") != 0
    capfd.readouterr()
    assert fixture.ledger.read_bytes() == before
    assert claimant.local_claimant_alive(native_ref) is None
    assert claimant.resolve_claimant_alive(native_ref, allow_remote=False) is None
    assert claimant.local_claimant_alive(guest_ref) is (status == "active")
    assert claimant.resolve_claimant_alive(guest_ref, allow_remote=False) is (status == "active")
    record = tracking.load_record(fixture.ledger)
    child = replace(record, status="active", owner_ref=native_ref, resources=[])
    verdict = prune.assess(
        child, git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.UNUSED),
        turn_count=0, claimant_alive=claimant.local_claimant_alive,
    )
    assert not verdict.safe and verdict.category == "claimed"
    assert fixture.ledger.read_bytes() == before


@pytest.mark.parametrize("canonical", [False, True])
def test_topology_alias_without_record_proof_cannot_inherit_dead_legacy_owner(
    local_worktree, monkeypatch, tmp_path, capfd, canonical,
):
    from agent_worktrees import claimant, legacy_wsl_record_locality, prune

    fixture = local_worktree
    _topology(fixture, "missing")
    for field in ("machine_identity_version", "environment", "execution_machine"):
        fixture.data.pop(field)
    fixture.data.update(machine="record-label", status="finalized", branch="worktree/different")
    fixture.ledger.write_text(yaml.safe_dump(fixture.data), encoding="utf-8")
    config = fixture.load()
    config = replace(
        config, machine="local-space", configured_machine="local-space",
        repos={"example": replace(config.default_repo, worktree_root=str(tmp_path / "new-root"))},
    )
    entry = cfg.MachineEntry(
        key="local-space", display_name="Local", hostname="new-host", alias="owner-alias",
        compatibility_aliases=["record-label"],
    )
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(cfg, "load_project_config", lambda *a, **k: config)
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda *a, **k: {entry.key: entry})
    machine = config.machine if canonical else entry.alias
    ref = f"{machine}/example/{fixture.checkout.name}"
    parsed = tracking.parse_claim_ref(ref)
    assert retained_checkout.resolve_owner(parsed, config) is None
    assert legacy_wsl_record_locality.resolve_legacy_wsl_owner_ref(parsed, config) is None
    monkeypatch.setattr(
        claimant, "_remote_claimant_alive",
        lambda *a, **k: pytest.fail("unproven local alias started a remote/self probe"),
    )
    before = fixture.ledger.read_bytes()
    assert execution_spaces.resolve_owner_record_path(ref, config)[0] is None
    assert claims_cli._claims_settle(Namespace(
        claim_owner_ref=ref, json=True, released=False,
    ), "retained-resource") != 0
    capfd.readouterr()
    assert claimant.local_claimant_alive(ref) is None
    assert claimant.resolve_claimant_alive(ref) is None
    child = replace(tracking.load_record(fixture.ledger), status="active", owner_ref=ref, resources=[])
    verdict = prune.assess(
        child, git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.UNUSED),
        turn_count=0, claimant_alive=claimant.local_claimant_alive,
    )
    assert not verdict.safe and verdict.category == "claimed"
    assert fixture.ledger.read_bytes() == before


@pytest.mark.parametrize("current_namespace", [False, True])
def test_branch_switch_does_not_veto_registered_local_entry(
    local_worktree, monkeypatch, tmp_path, capfd, current_namespace,
):
    from agent_worktrees import claimant, local_cache_refresh, resolve_cli

    fixture = local_worktree
    _topology(fixture, "malformed")
    checkout = tmp_path / "feature" / fixture.checkout.name
    original = f"worktree/registered-{current_namespace}"
    git_ops.git("worktree", "add", "-b", original, str(checkout), cwd=fixture.anchor)
    git_ops.git("switch", "-c", f"feature/switched-{current_namespace}", cwd=checkout)
    fixture.data.update(worktree_path=str(checkout), branch=original)
    fixture.ledger.write_text(yaml.safe_dump(fixture.data), encoding="utf-8")
    config = fixture.load()
    root = checkout.parent if current_namespace else tmp_path / "new-root"
    config = replace(config, repos={"example": replace(
        config.default_repo, worktree_root=str(root),
    )})
    _stub_launch_plumbing(monkeypatch, config)
    monkeypatch.setattr(cfg, "load_project_config", lambda *a, **k: config)
    monkeypatch.setattr(local_cache_refresh, "prepare_tool_roster", lambda *a, **k: None)
    code = resolve_cli.cmd_resolve(_args(
        worktree_id=fixture.checkout.name, no_resume=True, defer_projections=True,
    ))
    plan = json.loads(capfd.readouterr().out)
    assert code == 0, plan
    assert plan.get("launch", plan)["work_dir"] == str(checkout)
    record = tracking.load_record(fixture.ledger)
    assert record.branch == original
    owner = tracking.format_claim_ref(record.execution_machine, record.repo, record.worktree_id)
    assert claimant.local_claimant_alive(owner) is True
    data = yaml.safe_load(fixture.ledger.read_text(encoding="utf-8"))
    fixture.ledger.write_text(yaml.safe_dump({**data, "status": "finalized"}), encoding="utf-8")
    assert claimant.local_claimant_alive(owner) is None


@pytest.mark.parametrize("kind,selected_present", [
    ("anchor", False), ("anchor", True), ("ordinary", True),
])
def test_anchor_entry_never_relocates_to_another_project(
    local_worktree, monkeypatch, tmp_path, capfd, kind, selected_present,
):
    from agent_worktrees import installer, local_cache_refresh, resolve_cli, session_tracking_cli

    fixture = local_worktree
    _topology(fixture, "malformed")
    config = fixture.load()
    other_anchor = tmp_path / "other-anchor"
    other_anchor.mkdir()
    git_ops.git("init", cwd=other_anchor)
    git_ops.git(
        "-c", "user.name=Example", "-c", "user.email=example@example.com",
        "commit", "--allow-empty", "-m", "fixture", cwd=other_anchor,
    )
    other = replace(config, repo_name="other", repos={"other": replace(
        config.default_repo, anchor=str(other_anchor), worktree_root=str(tmp_path / "other-trees"),
    )})
    worktree_id = "@anchor" if kind == "anchor" else fixture.checkout.name
    other_path = other_anchor
    other_branch = fixture.data["branch"]
    if kind == "ordinary":
        other_path = Path(other.default_repo.worktree_root) / worktree_id
        other_branch = "worktree/other-same-id"
        git_ops.git("worktree", "add", "-b", other_branch, str(other_path), cwd=other_anchor)
    records = cfg.project_dir("other") / "worktrees"
    records.mkdir(parents=True)
    (records / f"{worktree_id}.yaml").write_text(yaml.safe_dump({
        **fixture.data, "worktree_id": worktree_id, "repo": "other",
        "kind": "anchor" if kind == "anchor" else "session",
        "worktree_path": str(other_path), "branch": other_branch, "resources": [],
    }), encoding="utf-8")
    if selected_present and kind == "anchor":
        (fixture.ledger.parent / "@anchor.yaml").write_text(yaml.safe_dump({
            **fixture.data, "worktree_id": "@anchor", "kind": "anchor",
            "worktree_path": str(fixture.anchor), "resources": [],
        }), encoding="utf-8")
    _stub_launch_plumbing(monkeypatch, config)
    monkeypatch.setattr(cfg, "tracking_dir", lambda name=None: cfg.project_dir(
        name or cfg.active_project(),
    ) / "worktrees")
    monkeypatch.setattr(cfg, "load_project_config", lambda name, **kwargs: (
        other if name == "other" else config
    ))
    monkeypatch.setattr(installer, "read_projects_registry", lambda: {
        "projects": {"example": {}, "other": {}},
    })
    monkeypatch.setattr(local_cache_refresh, "prepare_tool_roster", lambda *a, **k: None)
    code = resolve_cli.cmd_resolve(_args(
        worktree_id=worktree_id, machine="old-execution" if selected_present else None,
        no_resume=True, defer_projections=True,
    ))
    plan = json.loads(capfd.readouterr().out)
    if selected_present:
        assert code == 0, plan
        expected = fixture.anchor if kind == "anchor" else fixture.checkout
        assert plan.get("launch", plan)["work_dir"] == str(expected)
    else:
        assert code != 0 and "not found" in plan["error"].lower()
    assert cfg.active_project() == "example"
    if kind == "anchor":
        expected_ledger = fixture.ledger.parent / "@anchor.yaml" if selected_present else None
        assert session_tracking_cli._find_tracking_file_exact("@anchor") == expected_ledger
        assert session_tracking_cli._find_tracking_file("@anchor") == expected_ledger
