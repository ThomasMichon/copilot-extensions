"""Execution-space identity must not collapse through a shared OS hostname."""

from contextlib import contextmanager, nullcontext
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest
import yaml
from machine_transport.identity import is_local_machine
from machine_transport.registry import (
    MachineEntry, SSHEnvironment, machine_name, parse_machines_yaml_file,
)

from agent_worktrees import claims_cli, config as cfg, execution_spaces, tracking


def test_distinct_registered_spaces_are_not_local_through_shared_hostname():
    entries = {
        "workstation-windows": MachineEntry(
            key="workstation-windows",
            display_name="Workstation [Windows]",
            hostname="workstation",
        ),
        "workstation-wsl": MachineEntry(
            key="workstation-wsl",
            display_name="Workstation [WSL]",
            hostname="workstation",
        ),
    }
    assert not is_local_machine(
        "workstation-wsl",
        config_machine="workstation-windows",
        load_entries=lambda: entries,
        real_hostname="workstation",
    )


@pytest.fixture
def scoped_config(tmp_path, monkeypatch):
    entries = {
        key: MachineEntry(
            key=key, display_name=key, hostname="workstation",
            execution_platform=platform, physical_host="workstation",
        )
        for key, platform in (
            ("workstation-windows", "windows"), ("workstation-wsl", "wsl"),
        )
    }
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda anchor: entries)
    monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    config = cfg.Config(
        machine="workstation-windows", platform="windows", repo_name="project",
        srcroot=str(tmp_path), repos={"project": cfg.RepoConfig(
            anchor=str(tmp_path), worktree_root=str(tmp_path / "children"),
        )},
    )
    monkeypatch.setattr(cfg, "load_project_config", lambda project: config)
    return config


def test_explicit_registration_preserves_key_and_grouping_only_metadata(tmp_path):
    path = tmp_path / "machines.yaml"
    path.write_text(
        "machines:\n  workstation-windows:\n"
        "    execution_platform: windows\n    physical_host: workstation\n"
        "    alias: transport-alias\n",
        encoding="utf-8",
    )
    entry = parse_machines_yaml_file(path)["workstation-windows"]
    assert machine_name(entry) == "workstation-windows"
    assert entry.physical_host == "workstation"
    assert entry.execution_platform == "windows"


@pytest.mark.parametrize("owner", ["workstation", "workstation-wsl"])
def test_nonlocal_or_legacy_owner_never_looks_up_current_ledger(owner, scoped_config, monkeypatch):
    monkeypatch.setattr(
        cfg, "project_dir",
        lambda project: pytest.fail("nonlocal identity accessed local owner ledger"),
    )
    path, worktree_id, error = claims_cli._resolve_owner_ref_record_path(
        f"{owner}/project/wt-parent", scoped_config,
    )
    assert path is None
    assert worktree_id == "wt-parent"
    assert error


def test_true_same_registered_space_resolves_its_local_ledger(scoped_config, tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "project_dir", lambda project: tmp_path / project)
    path, worktree_id, error = claims_cli._resolve_owner_ref_record_path(
        "workstation-windows/project/wt-parent", scoped_config,
    )
    assert path == tmp_path / "project" / "worktrees" / "wt-parent.yaml"
    assert worktree_id == "wt-parent"
    assert error is None


@pytest.mark.parametrize("platform", ["windows", "wsl"])
def test_each_space_selects_only_its_own_execution_platform(platform, scoped_config, monkeypatch):
    scoped_config = replace(scoped_config, machine=f"workstation-{platform}", platform=platform)
    monkeypatch.setattr(cfg, "detect_platform", lambda: platform)
    execution_spaces.require_current_execution_space(scoped_config)
    assert execution_spaces.require_owner_identity(scoped_config.machine, scoped_config)
    other = "workstation-wsl" if platform == "windows" else "workstation-windows"
    assert not execution_spaces.require_owner_identity(other, scoped_config)


@pytest.mark.parametrize("machine", ["workstation", "workstation-wsl"])
def test_record_mutation_rejects_ambiguous_legacy_and_other_space(machine, scoped_config):
    record = SimpleNamespace(machine=machine, worktree_id="wt-child", owner_ref=None)
    with pytest.raises(execution_spaces.ExecutionSpaceError):
        execution_spaces.require_record_mutation(record, scoped_config)
    assert record.machine == machine


@pytest.mark.parametrize("owner", ["workstation", "workstation-wsl"])
def test_parent_settlement_cannot_mutate_wrong_ledger(owner, scoped_config, monkeypatch):
    from agent_worktrees import finalize

    record = SimpleNamespace(
        machine="workstation-windows", platform="windows", worktree_id="wt-child",
        owner_ref=f"{owner}/project/wt-parent",
    )
    monkeypatch.setattr(
        cfg, "project_dir",
        lambda project: pytest.fail("ambiguous settlement accessed owner ledger"),
    )
    with pytest.raises(execution_spaces.ExecutionSpaceError):
        finalize._settle_parent_obligation(record, scoped_config, "wt-child")


def test_qualified_reference_grammar_carries_space_key_unchanged():
    ref = tracking.format_claim_ref("workstation-wsl", "project", "wt-child")
    parsed = tracking.parse_claim_ref(ref)
    assert parsed.is_qualified
    assert parsed.machine == "workstation-wsl"
    assert parsed.project == "project"
    assert parsed.worktree_id == "wt-child"


def test_cross_space_owned_create_stops_before_readiness_or_source_fetch(
    tmp_path, scoped_config, monkeypatch,
):
    import agent_worktrees.__main__ as main

    anchor = tmp_path / "anchor"
    anchor.mkdir()
    config = cfg.Config(
        machine="workstation-windows", platform="windows", repo_name="project",
        srcroot=str(tmp_path),
        repos={"project": cfg.RepoConfig(
            anchor=str(anchor), worktree_root=str(tmp_path / "children"),
        )},
    )
    monkeypatch.setattr(
        main, "_coordination_readiness_for_owner_ref",
        lambda *args, **kwargs: pytest.fail("scope rejection did not precede readiness"),
    )
    monkeypatch.setattr(
        main, "_prepare_worktree_source",
        lambda *args, **kwargs: pytest.fail("cross-space creation fetched source"),
    )
    with pytest.raises(RuntimeError, match="cross-space"):
        main._create_worktree_core(
            config, no_mux=True,
            owner_ref="workstation-wsl/project/wt-parent",
            launch_preflight=main.LaunchPreflight(),
        )
    assert not (tmp_path / "children").exists()


def test_cleanup_identity_cannot_be_bypassed_by_current_physical_host(
    scoped_config, monkeypatch,
):
    monkeypatch.setattr(cfg, "load_config", lambda: scoped_config)
    record = SimpleNamespace(
        machine="workstation-wsl", worktree_id="wt-other", owner_ref=None,
    )
    with pytest.raises(execution_spaces.ExecutionSpaceError, match="different execution space"):
        execution_spaces.require_cleanup_identity(record, scoped_config.default_repo.anchor)


def test_handoff_equal_physical_labels_do_not_bypass_space_selection(scoped_config, monkeypatch):
    from agent_worktrees import claim_handoff_accept_support

    monkeypatch.setattr(cfg, "load_config", lambda: scoped_config)
    with pytest.raises(execution_spaces.ExecutionSpaceError):
        claim_handoff_accept_support.same_machine("workstation", "workstation")
    assert claim_handoff_accept_support.same_machine(
        "workstation-windows", "workstation-windows",
    )
    assert not claim_handoff_accept_support.same_machine(
        "workstation-windows", "workstation-wsl",
    )


def test_hostname_cannot_autoselect_declared_execution_space(scoped_config, monkeypatch):
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "workstation")
    with pytest.raises(execution_spaces.ExecutionSpaceError, match="explicit machine"):
        cfg.detect_machine(scoped_config.default_repo.anchor)


def test_legacy_multi_environment_registry_keeps_lifecycle_behavior(tmp_path, monkeypatch):
    entries = {"workstation": MachineEntry(
        key="workstation", display_name="Workstation",
        ssh_environments=[
            SSHEnvironment(name="windows", alias="workstation"),
            SSHEnvironment(name="wsl", alias="workstation-wsl"),
        ],
    )}
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda anchor: entries)
    config = SimpleNamespace(machine="workstation", default_repo=SimpleNamespace(anchor=tmp_path))
    record = SimpleNamespace(
        machine="workstation", worktree_id="legacy", owner_ref="workstation/project/parent",
    )
    assert execution_spaces.require_owner_identity("workstation", config)
    execution_spaces.require_record_mutation(record, config)
    execution_spaces.require_current_execution_space(config)


def test_invalid_registry_never_falls_back_to_shared_hostname(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "workstation")

    def invalid_registry(_anchor):
        raise ValueError("invalid execution_platform")

    monkeypatch.setattr(cfg, "load_machines_yaml", invalid_registry)
    with pytest.raises(ValueError, match="invalid execution_platform"):
        cfg.detect_machine(tmp_path)


@pytest.mark.parametrize(("machine", "platform"), [
    ("workstation", "windows"), ("workstation-wsl", "windows"),
    ("workstation-windows", "wsl"),
])
def test_unowned_create_requires_the_current_registered_space(
    machine, platform, tmp_path, scoped_config, monkeypatch,
):
    import agent_worktrees.__main__ as main

    anchor = tmp_path / "anchor"
    anchor.mkdir()
    config = cfg.Config(
        machine=machine, platform=platform, repo_name="project", srcroot=str(tmp_path),
        repos={"project": cfg.RepoConfig(
            anchor=str(anchor), worktree_root=str(tmp_path / "children"),
        )},
    )
    monkeypatch.setattr(
        main, "_prepare_worktree_source",
        lambda *args, **kwargs: pytest.fail("invalid space fetched source"),
    )
    with pytest.raises(execution_spaces.ExecutionSpaceError):
        main._create_worktree_core(config, no_mux=True, launch_preflight=main.LaunchPreflight())
    assert not (tmp_path / "children").exists()


def test_parent_record_authority_is_rechecked_before_settlement(
    tmp_path, scoped_config, monkeypatch,
):
    parent_ref = "workstation-windows/project/wt-parent"
    child = SimpleNamespace(
        machine="workstation-windows", platform="windows", worktree_id="wt-child",
        owner_ref=parent_ref, owner_claim_ref=tracking.parse_claim_ref(parent_ref),
    )
    parent = SimpleNamespace(
        machine="workstation-wsl", worktree_id="wt-parent", owner_ref=None,
    )
    parent_dir = tmp_path / "project"
    (parent_dir / "worktrees").mkdir(parents=True)
    (parent_dir / "worktrees" / "wt-parent.yaml").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cfg, "project_dir", lambda project: parent_dir)
    monkeypatch.setattr(tracking, "_RecordLock", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(tracking, "load_record", lambda path: parent)
    monkeypatch.setattr(
        tracking, "settle_resource_claim",
        lambda *args, **kwargs: pytest.fail("foreign parent ledger was mutated"),
    )
    output = SimpleNamespace(
        ok=lambda message: pytest.fail("foreign settlement reported success"),
        warn=lambda message: pytest.fail("authority failure was downgraded to warning"),
    )
    with pytest.raises(execution_spaces.ExecutionSpaceError, match="different execution space"):
        execution_spaces.settle_parent_obligation(child, scoped_config, "wt-child", output=output)


def test_known_parent_io_failure_reports_unconfirmed_settlement(
    tmp_path, scoped_config, monkeypatch,
):
    parent_ref = "workstation-windows/project/wt-parent"
    child = SimpleNamespace(
        machine="workstation-windows", platform="windows", worktree_id="wt-child",
        owner_ref=parent_ref, owner_claim_ref=tracking.parse_claim_ref(parent_ref),
    )
    parent_dir = tmp_path / "project"
    (parent_dir / "worktrees").mkdir(parents=True)
    (parent_dir / "worktrees" / "wt-parent.yaml").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cfg, "project_dir", lambda project: parent_dir)
    monkeypatch.setattr(tracking, "_RecordLock", lambda *args, **kwargs: nullcontext())

    def denied(_path):
        raise PermissionError("fixture denied")

    monkeypatch.setattr(tracking, "load_record", denied)
    warnings = []
    output = SimpleNamespace(
        ok=lambda message: pytest.fail("failed settlement reported success"), warn=warnings.append,
    )
    execution_spaces.settle_parent_obligation(child, scoped_config, "wt-child", output=output)
    assert len(warnings) == 1
    assert "Cannot confirm" in warnings[0]
    assert "PermissionError" in warnings[0]


@pytest.mark.parametrize(("body", "error"), [
    ("repo: project\n", "KeyError"),
    ("repo: project\nworktree_id: wt-parent\nbranch: worktree/wt-parent\n"
     "session_turns: {count: 1}\n", "TypeError"),
])
def test_malformed_parent_record_reports_unconfirmed_settlement(
    body, error, tmp_path, scoped_config, monkeypatch,
):
    parent_ref = "workstation-windows/project/wt-parent"
    child = SimpleNamespace(
        machine=scoped_config.machine, platform="windows", worktree_id="wt-child",
        owner_ref=parent_ref, owner_claim_ref=tracking.parse_claim_ref(parent_ref),
    )
    parent_dir = tmp_path / "project"
    (parent_dir / "worktrees").mkdir(parents=True)
    path = parent_dir / "worktrees" / "wt-parent.yaml"
    path.write_text(body, encoding="utf-8")
    before = path.read_bytes()
    monkeypatch.setattr(cfg, "project_dir", lambda project: parent_dir)
    monkeypatch.setattr(tracking, "_RecordLock", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(
        tracking, "settle_resource_claim",
        lambda *args, **kwargs: pytest.fail("malformed parent ledger was mutated"),
    )
    warnings = []
    output = SimpleNamespace(
        ok=lambda message: pytest.fail("malformed settlement reported success"),
        warn=warnings.append,
    )
    execution_spaces.settle_parent_obligation(child, scoped_config, "wt-child", output=output)
    assert len(warnings) == 1
    assert "Cannot confirm" in warnings[0] and error in warnings[0]
    assert path.read_bytes() == before


@pytest.mark.parametrize("error", [KeyError("machine"), TypeError("malformed config")])
@pytest.mark.parametrize("loader", ["load_machines_yaml", "load_config"])
def test_cleanup_known_format_failure_is_structured_refusal(
    error, loader, scoped_config, monkeypatch,
):
    def malformed(*args, **kwargs):
        raise error

    monkeypatch.setattr(cfg, loader, malformed)
    with pytest.raises(execution_spaces.ExecutionSpaceError) as result:
        execution_spaces.require_cleanup_identity(
            SimpleNamespace(), scoped_config.default_repo.anchor,
        )
    assert result.value.__cause__ is error


def test_cleanup_malformed_registry_parser_is_structured_refusal(tmp_path, monkeypatch):
    path = tmp_path / "machines.yaml"
    path.write_text(
        "machines:\n  workstation-windows:\n    execution_platform: windows\n"
        "    ssh:\n      environments: 42\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda anchor: parse_machines_yaml_file(path))
    with pytest.raises(execution_spaces.ExecutionSpaceError, match="registry is invalid") as result:
        execution_spaces.require_cleanup_identity(SimpleNamespace(), tmp_path)
    assert isinstance(result.value.__cause__, TypeError)


@pytest.mark.parametrize("loader", ["load_machines_yaml", "load_config"])
def test_cleanup_does_not_suppress_execution_space_error(loader, scoped_config, monkeypatch):
    error = execution_spaces.ExecutionSpaceError("authority refused")

    def refused(*args, **kwargs):
        raise error

    monkeypatch.setattr(cfg, loader, refused)
    with pytest.raises(execution_spaces.ExecutionSpaceError) as result:
        execution_spaces.require_cleanup_identity(SimpleNamespace(), scoped_config.default_repo.anchor)
    assert result.value is error


@pytest.mark.parametrize("key", ["", " space", "space key", "space/key", r"space\key", "space#session"])
def test_explicit_registry_rejects_noncanonical_keys(key, tmp_path):
    path = tmp_path / "machines.yaml"
    path.write_text(
        yaml.safe_dump({"machines": {key: {"execution_platform": "windows"}}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="canonical names"):
        parse_machines_yaml_file(path)


def test_explicit_registry_rejects_casefold_key_collisions(tmp_path):
    path = tmp_path / "machines.yaml"
    path.write_text(
        yaml.safe_dump({"machines": {
            "workstation": {"execution_platform": "windows"},
            "WORKSTATION": {"execution_platform": "wsl"},
        }}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unique without regard to case"):
        parse_machines_yaml_file(path)


def test_handoff_rejects_transport_alias_authority(scoped_config, monkeypatch):
    from dataclasses import replace
    from agent_worktrees import claim_handoff_accept_support

    entries = cfg.load_machines_yaml(scoped_config.default_repo.anchor)
    entries["workstation-windows"] = replace(
        entries["workstation-windows"], alias="windows-ssh",
    )
    monkeypatch.setattr(cfg, "load_config", lambda: scoped_config)
    with pytest.raises(execution_spaces.ExecutionSpaceError, match="registered|explicit"):
        claim_handoff_accept_support.same_machine("windows-ssh", "windows-ssh")


@pytest.mark.parametrize("verb", ["add", "release", "settle"])
def test_claim_write_rechecks_owner_after_acquiring_lock(
    verb, tmp_path, scoped_config, monkeypatch,
):
    from agent_worktrees import tracking_claim_write

    record = SimpleNamespace(
        machine=scoped_config.machine, platform="windows", repo="project", worktree_id="wt-owner",
        owner_ref=None,
    )
    lock_held = False

    @contextmanager
    def raced_lock(*args, **kwargs):
        nonlocal lock_held
        record.owner_ref = "workstation-wsl/project/wt-new-parent"
        lock_held = True
        try:
            yield
        finally:
            lock_held = False

    def fresh_record(path):
        assert lock_held
        return record

    monkeypatch.setattr(tracking, "_RecordLock", raced_lock)
    monkeypatch.setattr(tracking, "load_record", fresh_record)
    monkeypatch.setattr(
        tracking, "save_record",
        lambda *args, **kwargs: pytest.fail("foreign claim transaction saved a record"),
    )
    result = getattr(tracking_claim_write, f"apply_claim_{verb}")({
        "worktree_id": record.worktree_id, "yaml_path": str(tmp_path / "wt-owner.yaml"),
        "kind": "pr", "ref": "owner/example#1", "disposition": "at-rest",
    })
    assert result["error"] == "rejected"
    assert "cross-space" in result["message"]


@pytest.mark.parametrize("role", ["source", "consumer", "child"])
def test_handoff_loader_rejects_fresh_foreign_record(
    role, tmp_path, scoped_config, monkeypatch,
):
    from agent_worktrees import claim_handoffs

    record = SimpleNamespace(
        machine="workstation-wsl", repo="project", worktree_id="wt-foreign",
        owner_ref=None, status="active",
    )
    monkeypatch.setattr(tracking, "load_record", lambda path: record)
    with pytest.raises(claim_handoffs.ClaimHandoffError, match="different execution space"):
        claim_handoffs._load_record_from_path(tmp_path / "wt-foreign.yaml", role=role)


@pytest.mark.parametrize("seed", [None, "new resume intent"])
def test_foreign_resume_rejects_before_seed_staging(
    seed, tmp_path, scoped_config, monkeypatch,
):
    from agent_worktrees import resolve_launch_cli

    record = SimpleNamespace(
        machine="workstation-wsl", repo="project", worktree_id="wt-foreign",
        owner_ref=None, worktree_path=str(tmp_path), yaml_path=tmp_path / "wt-foreign.yaml",
        pending_seed="legacy intent",
    )
    plans = []
    monkeypatch.setattr(
        resolve_launch_cli, "seed_for_attempt",
        lambda *args, **kwargs: pytest.fail("foreign resume staged a launch seed"),
    )
    monkeypatch.setattr(resolve_launch_cli, "_emit_plan", plans.append)
    result = resolve_launch_cli._resolve_resume_context(
        resolve_launch_cli.ResolveLaunchContext(
            config=scoped_config, args=SimpleNamespace(seed=seed), record=record,
        )
    )
    assert result == 3
    assert plans[-1]["action"] == "error"
    assert "different execution space" in plans[-1]["error"]


def test_source_accept_rejects_foreign_record_before_registry_transition(
    tmp_path, scoped_config, monkeypatch,
):
    from agent_worktrees import claim_handoffs

    project_dir = tmp_path / "project"
    tracking_dir = project_dir / "worktrees"
    tracking_dir.mkdir(parents=True)
    source_path = tracking_dir / "wt-source.yaml"
    source = tracking.create_new_record(
        "wt-source", "worktree/wt-source", str(tmp_path), "project",
        "workstation-wsl", "wsl", tracking_dir,
    )
    claim = tracking.ResourceClaim(
        kind="pr", ref="owner/example#1", created_at="2026-10-09T12:00:00",
        state="active", handoff_bundle="bundle-1",
    )
    source.resources = [claim]
    tracking.save_record(source, source_path)
    bundle = claim_handoffs.ClaimBundle(
        bundle_id="bundle-1", state="offered",
        source="workstation-windows/project/wt-source",
        consumer="workstation-windows/project/wt-consumer",
        claims=(claim_handoffs._claim_snapshot(claim),),
        offered_at="2026-10-09T12:00:00", updated_at="2026-10-09T12:00:00",
    )
    registry = tmp_path / "claim-handoffs.yaml"
    claim_handoffs._save_registry(registry, [bundle])
    before = source_path.read_bytes(), registry.read_bytes()
    monkeypatch.setattr(cfg, "project_dir", lambda project: project_dir)
    monkeypatch.setattr(claim_handoffs, "registry_path", lambda: registry)
    with pytest.raises(claim_handoffs.ClaimHandoffError, match="different execution space"):
        claim_handoffs.accept_source(bundle.bundle_id, actor=bundle.consumer)
    assert (source_path.read_bytes(), registry.read_bytes()) == before


def test_project_authority_failure_is_an_explicit_rejection(scoped_config, monkeypatch):
    def unavailable(project):
        raise ValueError("project is not registered")

    monkeypatch.setattr(cfg, "load_project_config", unavailable)
    with pytest.raises(execution_spaces.ExecutionSpaceError, match="receiving-side authority"):
        execution_spaces.require_project_record_mutation(SimpleNamespace(repo="missing"))


@pytest.mark.parametrize("scoped", [False, True])
def test_project_authority_rejects_unrelated_sole_repo(scoped, tmp_path, monkeypatch):
    unrelated = cfg.RepoConfig(anchor=str(tmp_path), worktree_root=str(tmp_path / "children"))
    config = cfg.Config(
        srcroot=str(tmp_path), machine="workstation-windows", platform="windows",
        repo_name="missing", repos={"unrelated": unrelated},
    )
    assert config.default_repo is unrelated
    monkeypatch.setattr(cfg, "load_project_config", lambda project: config)
    entries = {"workstation-windows": MachineEntry(
        key="workstation-windows", display_name="Windows",
        execution_platform="windows" if scoped else "",
    )}
    registry_reads = []
    monkeypatch.setattr(
        cfg, "load_machines_yaml",
        lambda anchor: registry_reads.append(anchor) or entries,
    )
    record = SimpleNamespace(
        repo="missing", machine=config.machine, platform="windows", worktree_id="wt-owner",
        owner_ref=None,
    )
    with pytest.raises(execution_spaces.ExecutionSpaceError, match="requested project is not registered"):
        execution_spaces.require_project_record_mutation(record)
    assert registry_reads == []


def test_project_authority_uses_requested_repo_anchor(scoped_config, tmp_path, monkeypatch):
    requested_anchor = scoped_config.repos["project"].anchor
    scoped_entries = cfg.load_machines_yaml(requested_anchor)
    unrelated_anchor = str(tmp_path / "unrelated")
    scoped_config.repos["unrelated"] = cfg.RepoConfig(
        anchor=unrelated_anchor, worktree_root=str(tmp_path / "other-children"),
    )
    scoped_config = replace(scoped_config, repo_name="unrelated")
    monkeypatch.setattr(cfg, "load_project_config", lambda project: scoped_config)
    anchors = []

    def registry(anchor):
        anchors.append(anchor)
        return scoped_entries if anchor == requested_anchor else {}

    monkeypatch.setattr(cfg, "load_machines_yaml", registry)
    record = SimpleNamespace(
        repo="project", machine="workstation-wsl", platform="wsl", worktree_id="wt-other",
        owner_ref=None,
    )
    with pytest.raises(execution_spaces.ExecutionSpaceError, match="different execution space"):
        execution_spaces.require_project_record_mutation(record)
    assert anchors and set(anchors) == {requested_anchor}
    assert scoped_config.repo_name == "unrelated"
    record.machine, record.platform = "workstation-windows", "windows"
    execution_spaces.require_project_record_mutation(record)


def test_sweep_rechecks_fresh_owner_under_lock(
    tmp_path, scoped_config, monkeypatch, capfd,
):
    from agent_worktrees import sweep

    stale = SimpleNamespace(
        machine=scoped_config.machine, platform="windows", repo="project", worktree_id="wt-owner",
        owner_ref=None, resources=[],
    )
    fresh = SimpleNamespace(
        machine="workstation-wsl", repo="project", worktree_id=stale.worktree_id,
        owner_ref=None,
    )
    monkeypatch.setattr(cfg, "load_config", lambda: scoped_config)
    monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
    monkeypatch.setattr(tracking, "list_records", lambda path: [stale])
    monkeypatch.setattr(tracking, "_RecordLock", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(tracking, "load_record", lambda path: fresh)
    monkeypatch.setattr(sweep, "make_resolvers", lambda config: (lambda claim: True, lambda claim: True))
    monkeypatch.setattr(
        tracking, "sweep_abandoned_obligations",
        lambda *args, **kwargs: pytest.fail("foreign sweep changed the fresh claim ledger"),
    )
    assert claims_cli._claims_sweep(SimpleNamespace(apply=True, json=True)) == 1
    assert "different execution space" in json.loads(capfd.readouterr().out)["error"]


def test_cross_space_owned_handoff_rejects_before_source_release(
    tmp_path, scoped_config, monkeypatch,
):
    from agent_worktrees import claim_handoffs

    bundle = claim_handoffs.ClaimBundle(
        bundle_id="bundle-1", state="offered",
        source="workstation-wsl/project/wt-source",
        consumer="workstation-windows/project/wt-consumer",
        claims=({"kind": "worktree", "ref": "workstation-windows/project/wt-child",
                 "created_at": "", "state": "active", "note": ""},),
        offered_at="2026-10-09T12:00:00", updated_at="2026-10-09T12:00:00",
    )
    registry = tmp_path / "claim-handoffs.yaml"
    claim_handoffs._save_registry(registry, [bundle])
    before = registry.read_bytes()
    monkeypatch.setattr(claim_handoffs, "registry_path", lambda: registry)
    for name in ("acquire_bundle_fence", "accept_source", "remote_accept_source"):
        monkeypatch.setattr(
            claim_handoffs, name,
            lambda *args, **kwargs: pytest.fail("unsupported handoff began releasing source authority"),
        )
    with pytest.raises(claim_handoffs.ClaimHandoffError, match="source claims are retained"):
        claim_handoffs.accept(bundle.bundle_id, actor=bundle.consumer, machine=scoped_config.machine)
    assert registry.read_bytes() == before


@pytest.mark.parametrize("verb", [
    "launch_seed_stage", "launch_seed_take", "launch_seed_restore",
    "launch_seed_finish", "launch_seed_remove",
])
def test_seed_transaction_rechecks_owner_after_acquiring_lock(
    verb, tmp_path, scoped_config, monkeypatch,
):
    from agent_worktrees import launch_seed_state, tracking_write

    path = tmp_path / "wt-owner.yaml"
    path.write_text("record before", encoding="utf-8")
    target = launch_seed_state.state_path(path)
    target.parent.mkdir()
    target.write_text("seed before", encoding="utf-8")
    record = SimpleNamespace(
        machine=scoped_config.machine, platform="windows", repo="project", worktree_id=path.stem,
        owner_ref=None,
    )
    lock_held = False

    @contextmanager
    def raced_lock(*args, **kwargs):
        nonlocal lock_held
        record.owner_ref = "workstation-wsl/project/wt-new-parent"
        lock_held = True
        try:
            yield
        finally:
            lock_held = False

    def fresh_record(record_path):
        assert lock_held
        return record

    monkeypatch.setattr(tracking, "_RecordLock", raced_lock)
    monkeypatch.setattr(tracking, "load_record", fresh_record)
    monkeypatch.setattr(
        tracking, "save_record",
        lambda *args, **kwargs: pytest.fail("foreign seed transaction saved a record"),
    )
    monkeypatch.setattr(
        launch_seed_state, "_write",
        lambda *args, **kwargs: pytest.fail("foreign seed transaction wrote a sidecar"),
    )
    result = tracking_write.run_direct(verb, {
        "worktree_id": path.stem, "yaml_path": str(path), "remove_record": True,
    }, reason="fixture authority race")
    assert result["error"] == "execution_space"
    assert "cross-space" in result["message"]
    assert path.read_text(encoding="utf-8") == "record before"
    assert target.read_text(encoding="utf-8") == "seed before"


def test_seed_daemon_returns_known_authority_rejection_without_writing(
    tmp_path, scoped_config, monkeypatch,
):
    from agent_worktrees import launch_seed_state, locks, status_monitor_runtime, tracking_write

    record = tracking.create_new_record(
        "wt-owner", "worktree/wt-owner", str(tmp_path), "project",
        scoped_config.machine, "windows", tmp_path,
        owner_ref="workstation-wsl/project/wt-parent",
    )
    before = record.yaml_path.read_bytes()
    server = tracking_write.start_server(tracking_write.compute)
    server.start()
    endpoint = tracking_write.rendezvous_fields(server)
    monkeypatch.setattr(locks, "read_lock", lambda *args: endpoint)
    monkeypatch.setattr(status_monitor_runtime, "_status_monitor_enabled", lambda: False)
    monkeypatch.setattr(
        tracking_write, "run_direct",
        lambda *args, **kwargs: pytest.fail("authority rejection fell back after daemon dispatch"),
    )
    try:
        with pytest.raises(ValueError, match="cross-space"):
            launch_seed_state.stage(record.yaml_path, kind="resume", text="must remain unstaged")
    finally:
        server.close()
    assert record.yaml_path.read_bytes() == before
    assert not launch_seed_state.state_path(record.yaml_path).exists()


@pytest.mark.parametrize("verb", ["release", "settle"])
@pytest.mark.parametrize("json_out", [False, True])
def test_claim_cli_reports_authority_rejection_without_success_or_crash(
    verb, json_out, tmp_path, scoped_config, monkeypatch, capfd,
):
    from agent_worktrees import tracking_claim_write, worktree_identity

    path = tmp_path / "wt-foreign.yaml"
    path.write_text("unchanged", encoding="utf-8")
    foreign = SimpleNamespace(
        machine="workstation-wsl", platform="wsl", repo="project",
        worktree_id=path.stem, owner_ref=None,
    )
    monkeypatch.setattr(cfg, "load_config", lambda: scoped_config)
    monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
    monkeypatch.setattr(worktree_identity, "_infer_worktree_id", lambda *args: path.stem)
    monkeypatch.setattr(tracking, "load_record", lambda *args: foreign)
    monkeypatch.setattr(
        claims_cli, "_dispatch_claim",
        lambda name, args: getattr(tracking_claim_write, f"apply_{name}")(args),
    )
    result = getattr(claims_cli, f"_claims_{verb}")(
        SimpleNamespace(json=json_out, remove=False, released=False), "owner/example#1",
    )
    output = capfd.readouterr().out
    assert result == 1
    if json_out:
        assert "different execution space" in json.loads(output)["error"]
    else:
        assert "different execution space" in output
    assert "settled outbound" not in output
    assert path.read_text(encoding="utf-8") == "unchanged"


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("failure", [ValueError, PermissionError, yaml.YAMLError])
def test_cleanup_registry_failure_is_a_structured_refusal(
    force, failure, tmp_path, scoped_config, monkeypatch,
):
    from agent_worktrees import cleanup_gc_cli

    record = tracking.create_new_record(
        "wt-owner", "worktree/wt-owner", str(tmp_path), "project",
        scoped_config.machine, "windows", tmp_path,
    )

    def invalid_registry(anchor):
        raise failure("cannot validate registry")

    monkeypatch.setattr(cfg, "load_machines_yaml", invalid_registry)
    result = cleanup_gc_cli._revalidate_cleanup_safety(
        record.worktree_id, repo=SimpleNamespace(anchor=str(tmp_path)),
        tracking_path=tmp_path, force=force,
        reap=lambda *args: pytest.fail("cleanup reached destructive work without registry authority"),
    )
    assert not result.cleanable
    assert result.bucket == "execution-space"
    assert "registry is invalid" in result.reason
    assert record.yaml_path.exists()


def test_forced_cleanup_rechecks_fresh_authority_inside_sidecar_lock(
    tmp_path, scoped_config, monkeypatch,
):
    from agent_worktrees import cleanup_gc_cli

    record = tracking.create_new_record(
        "wt-owner", "worktree/wt-owner", str(tmp_path), "project",
        scoped_config.machine, "windows", tmp_path,
    )
    monkeypatch.setattr(cfg, "load_config", lambda: scoped_config)
    real_lock = tracking._RecordLock
    locked = False

    @contextmanager
    def raced_lock(path, *, require_sidecar):
        nonlocal locked
        assert require_sidecar is True
        fresh = yaml.safe_load(path.read_text(encoding="utf-8"))
        fresh["machine"], fresh["platform"] = "workstation-wsl", "wsl"
        path.write_text(yaml.safe_dump(fresh), encoding="utf-8")
        with real_lock(path, require_sidecar=require_sidecar):
            locked = True
            try:
                yield
            finally:
                locked = False

    monkeypatch.setattr(tracking, "_RecordLock", raced_lock)
    original_load = tracking.load_record

    def fresh_load(path):
        assert locked
        return original_load(path)

    monkeypatch.setattr(tracking, "load_record", fresh_load)
    result = cleanup_gc_cli._revalidate_cleanup_safety(
        record.worktree_id, repo=scoped_config.default_repo, tracking_path=tmp_path, force=True,
        reap=lambda *args: pytest.fail("foreign forced cleanup began deleting checkout or branch"),
    )
    assert not result.cleanable and result.bucket == "execution-space"
    assert "different execution space" in result.reason
    assert record.yaml_path.exists()


@pytest.mark.parametrize("via_daemon", [False, True])
@pytest.mark.parametrize("verb", [
    "session_conclude", "session_link_succession", "session_resolve_handoff_successor",
])
def test_session_lifecycle_fences_raced_fresh_foreign_record(
    verb, via_daemon, tmp_path, scoped_config, monkeypatch,
):
    from agent_worktrees import tracking_session_lifecycle_write, tracking_write

    record = tracking.create_new_record(
        "wt-owner", "worktree/wt-owner", str(tmp_path), "project",
        scoped_config.machine, "windows", tmp_path,
    )
    record.sessions = [tracking.SessionEntry("session-1", "2026-01-01T00:00:00")]
    record.head_session = "session-1"
    if verb == "session_link_succession":
        record.sessions.append(tracking.SessionEntry("session-2", "2026-01-01T00:00:00"))
    elif verb == "session_resolve_handoff_successor":
        record.status, record.kind = "finalized", "bridge"
        tracking.open_handoff(record, "session-1", "token-1", save=False)
    expected_state = record.session_entry("session-1").state
    expected_head = record.head_session
    tracking.save_record(record)
    real_lock = tracking._RecordLock
    foreign_bytes = []

    @contextmanager
    def raced_lock(path, *, require_sidecar):
        assert require_sidecar is True
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        raw["machine"], raw["platform"] = "workstation-wsl", "wsl"
        path.write_text(yaml.safe_dump(raw), encoding="utf-8")
        foreign_bytes.append(path.read_bytes())
        with real_lock(path, require_sidecar=True):
            yield

    monkeypatch.setattr(tracking, "_RecordLock", raced_lock)
    args = {
        "worktree_id": record.worktree_id, "yaml_path": str(record.yaml_path),
        "session_id": "session-1", "state": "concluded", "only_if_active": True,
        "predecessor": "session-1", "successor": "session-2",
        "handoff_token": "token-1", "successor_id": "session-2",
    }
    monkeypatch.setattr(
        tracking, "save_record",
        lambda *args, **kwargs: pytest.fail("foreign lifecycle repair saved a record"),
    )
    monkeypatch.setattr(
        tracking, "record_pr_claims_reassigned",
        lambda *args, **kwargs: pytest.fail("foreign lifecycle repair reassigned claim head"),
    )
    if via_daemon:
        server = tracking_write.start_server(tracking_write.compute)
        server.start()
        try:
            fields = tracking_write.rendezvous_fields(server)
            assert fields["tracking_write_verb_versions"][verb] == 2
            result = tracking_write.dispatch(
            verb, args, read_lock_data=lambda: fields,
            ensure_monitor=None, min_version=2,
            )
        finally:
            server.close()
    else:
        handler = {
            "session_conclude": tracking_session_lifecycle_write.apply_session_conclude,
            "session_link_succession": tracking_session_lifecycle_write.apply_session_link_succession,
            "session_resolve_handoff_successor": tracking_session_lifecycle_write.apply_resolve_handoff_successor,
        }[verb]
        result = handler(args)
    assert result["error"] == "execution_space"
    assert "different execution space" in result["message"]
    assert record.yaml_path.read_bytes() == foreign_bytes[-1]
    latest = tracking.load_record(record.yaml_path)
    assert latest.session_entry("session-1").state == expected_state
    assert latest.head_session == expected_head


@pytest.mark.parametrize(("caller", "verb"), [
    ("handoff", "session_conclude"), ("cli", "session_conclude"),
    ("cli", "session_link_succession"), ("cli", "session_resolve_handoff_successor"),
])
def test_session_lifecycle_production_callers_never_dial_old_daemon(
    caller, verb, tmp_path, scoped_config, monkeypatch,
):
    import threading
    from agent_worktrees import (
        handoff_cutover, locks, session_tracking_cli, status_monitor_runtime,
        tracking_write,
    )

    record = tracking.create_new_record(
        "wt-owner", "worktree/wt-owner", str(tmp_path), "project",
        "workstation-wsl", "wsl", tmp_path,
    )
    record.sessions = [tracking.SessionEntry("session-1", "2026-01-01T00:00:00")]
    record.head_session = "session-1"
    tracking.save_record(record)
    before = record.yaml_path.read_bytes()
    threads = []
    handler = tracking_write._VERBS[verb]

    def conclude(args):
        threads.append(threading.get_ident())
        return handler(args)

    monkeypatch.setitem(tracking_write._VERBS, verb, conclude)
    server = tracking_write.start_server(tracking_write.compute)
    server.start()
    try:
        fields = tracking_write.rendezvous_fields(server)
        fields["tracking_write_verb_versions"] = {verb: 1}
        monkeypatch.setattr(locks, "read_lock", lambda path: fields)
        monkeypatch.setattr(status_monitor_runtime, "_status_monitor_enabled", lambda: False)
        adapter = (
            handoff_cutover._dispatch_handoff_repair if caller == "handoff"
            else session_tracking_cli._dispatch_session_lifecycle
        )
        result = adapter(verb, {
            "worktree_id": record.worktree_id, "yaml_path": str(record.yaml_path),
            "session_id": "session-1", "state": "concluded",
            "predecessor": "session-1", "successor": "session-2",
            "handoff_token": "token-1", "successor_id": "session-2",
        })
    finally:
        server.close()
    assert threads == [threading.get_ident()]
    assert result["error"] == "execution_space"
    assert record.yaml_path.read_bytes() == before


@pytest.mark.parametrize("verb", ["conclude", "link"])
def test_session_lifecycle_cli_reports_authority_refusal_without_success(
    verb, tmp_path, scoped_config, monkeypatch, capfd,
):
    from agent_worktrees import session_tracking_cli

    record = tracking.create_new_record(
        "wt-owner", "worktree/wt-owner", str(tmp_path), "project",
        "workstation-wsl", "wsl", tmp_path,
    )
    record.sessions = [tracking.SessionEntry("session-1", "2026-01-01T00:00:00")]
    record.head_session = "session-1"
    tracking.save_record(record)
    before = record.yaml_path.read_bytes()
    monkeypatch.setattr(session_tracking_cli, "_find_tracking_file", lambda raw: record.yaml_path)
    command = (
        session_tracking_cli.cmd_conclude_session if verb == "conclude"
        else session_tracking_cli.cmd_link_succession
    )
    result = command(SimpleNamespace(
        worktree_id=record.worktree_id, session_id="session-1", state="concluded",
        predecessor="session-1", successor="session-2",
    ))
    assert result == 1
    report = json.loads(capfd.readouterr().out)
    assert "different execution space" in report["error"]
    assert "state" not in report
    assert record.yaml_path.read_bytes() == before


@pytest.mark.parametrize("platform", ["wsl", None])
def test_matching_key_does_not_authorize_an_ambiguous_legacy_platform(platform, scoped_config):
    record = SimpleNamespace(
        machine=scoped_config.machine, platform=platform, repo="project",
        worktree_id="legacy-shared-key", owner_ref=None,
    )
    with pytest.raises(execution_spaces.ExecutionSpaceError, match="execution platform"):
        execution_spaces.require_record_mutation(record, scoped_config)


def test_configured_platform_must_match_actual_selected_space(scoped_config):
    scoped_config = replace(scoped_config, platform="wsl")
    with pytest.raises(execution_spaces.ExecutionSpaceError, match="execution platform"):
        execution_spaces.require_current_execution_space(scoped_config)


def test_finalize_session_settlement_propagates_authority_rejection(
    tmp_path, scoped_config, monkeypatch,
):
    from agent_worktrees import finalize, tracking_write

    record = SimpleNamespace(
        machine=scoped_config.machine, platform="windows", repo="project",
        worktree_id="wt-owner", owner_ref=None,
    )
    monkeypatch.setattr(
        tracking_write, "dispatch",
        lambda *args, **kwargs: {"error": "rejected", "message": "foreign ledger"},
    )
    with pytest.raises(execution_spaces.ExecutionSpaceError, match="foreign ledger"):
        finalize._settle_current_session_claim(tmp_path / "wt-owner.yaml", record, "session-1")


@pytest.mark.parametrize("platform", ["wsl", None])
def test_pr_reconciliation_never_persists_foreign_fresh_record(
    platform, tmp_path, scoped_config, monkeypatch, caplog,
):
    from agent_worktrees import prune

    record = tracking.create_new_record(
        "wt-owner", "worktree/wt-owner", str(tmp_path), "project",
        scoped_config.machine, "windows", tmp_path,
    )
    record.prs = [tracking.PRRecord(number=1, repo="owner/example", state="open")]
    tracking.save_record(record)
    fresh = tracking.load_record(record.yaml_path)
    fresh.platform = platform
    tracking.save_record(fresh)
    before = record.yaml_path.read_bytes()
    changes = prune.reconcile_and_persist_best_effort(
        record, lambda *args: SimpleNamespace(merged=True),
    )
    assert changes == [(1, "open", "merged")]
    assert record.yaml_path.read_bytes() == before
    assert "execution platform" in caplog.text


@pytest.mark.parametrize("owner_locked", [False, True])
def test_reciprocal_claim_rejects_foreign_record_behind_current_space_reference(
    owner_locked, tmp_path, scoped_config, monkeypatch, capfd,
):
    from agent_worktrees import worktree_creation

    monkeypatch.setattr(cfg, "project_dir", lambda project: tmp_path)
    path = tmp_path / "worktrees"
    path.mkdir()
    record = tracking.create_new_record(
        "wt-parent", "worktree/wt-parent", str(tmp_path), "project",
        scoped_config.machine, "wsl", path,
    )
    before = record.yaml_path.read_bytes()
    assert not worktree_creation._journal_owner_reciprocal_claim(
        scoped_config, "wt-child", f"{scoped_config.machine}/project/wt-parent",
        owner_locked=owner_locked,
    )
    assert record.yaml_path.read_bytes() == before
    assert "execution platform" in capfd.readouterr().err


def test_run_claim_journal_rejects_foreign_fresh_owner(tmp_path, scoped_config, monkeypatch):
    from agent_worktrees import worktree_ops_cli

    monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
    record = tracking.create_new_record(
        "wt-parent", "worktree/wt-parent", str(tmp_path), "project",
        scoped_config.machine, "wsl", tmp_path,
    )
    before = record.yaml_path.read_bytes()
    with pytest.raises(execution_spaces.ExecutionSpaceError, match="execution platform"):
        worktree_ops_cli._journal_run_claim(
            f"{scoped_config.machine}/project/wt-parent",
            json.dumps({"worktree": {
                "id": "wt-child", "machine": scoped_config.machine, "repo": "project",
            }}),
        )
    assert record.yaml_path.read_bytes() == before


@pytest.mark.parametrize("race_after_creation", [False, True])
def test_run_reservation_and_settlement_fence_fresh_owner(
    race_after_creation, tmp_path, scoped_config, monkeypatch, capfd,
):
    from agent_worktrees import worktree_ops_cli

    monkeypatch.setattr(cfg, "load_config", lambda: scoped_config)
    monkeypatch.setattr(cfg, "project_dir", lambda project: tmp_path)
    monkeypatch.setattr(
        claims_cli, "_coordination_readiness_for_owner_ref",
        lambda *args: SimpleNamespace(ready=True),
    )
    record = tracking.create_new_record(
        "wt-parent", "worktree/wt-parent", str(tmp_path), "project",
        scoped_config.machine, "windows" if race_after_creation else "wsl",
        tmp_path / "worktrees",
    )
    before = record.yaml_path.read_bytes()
    created = []
    raced_bytes = []

    def create_resource(*args, **kwargs):
        assert race_after_creation, "unauthorized owner reached resource creation"
        fresh = tracking.load_record(record.yaml_path)
        assert len(fresh.resources) == 1
        assert fresh.resources[0].ref.startswith("pending-run:")
        fresh.platform = "wsl"
        tracking.save_record(fresh)
        raced_bytes.append(record.yaml_path.read_bytes())
        created.append(True)
        return SimpleNamespace(returncode=0, stdout=json.dumps({"worktree": {
            "id": "wt-child", "machine": scoped_config.machine, "repo": "project",
        }}))

    monkeypatch.setattr(worktree_ops_cli.subprocess, "run", create_resource)
    assert worktree_ops_cli.cmd_run(SimpleNamespace(
        inner_command=["new --json"],
        owner_ref=f"{scoped_config.machine}/project/wt-parent",
    )) == 1
    assert bool(created) == race_after_creation
    assert record.yaml_path.read_bytes() == (raced_bytes[0] if race_after_creation else before)
    assert "execution platform" in capfd.readouterr().out


@pytest.mark.parametrize("json_mode", [False, True])
def test_resume_reports_raced_authority_rejection_as_structured_error(
    json_mode, tmp_path, scoped_config, monkeypatch, capfd,
):
    from agent_worktrees import resolve_cli, resolve_launch_cli

    record = tracking.create_new_record(
        "wt-owner", "worktree/wt-owner", str(tmp_path), "project",
        scoped_config.machine, "windows", tmp_path,
    )
    before = record.yaml_path.read_bytes()
    fresh = tracking.load_record(record.yaml_path)
    fresh.owner_ref = "workstation-wsl/project/wt-new-parent"
    module = resolve_cli if json_mode else resolve_launch_cli
    monkeypatch.setattr(module, "seed_for_attempt", lambda *args: None)
    monkeypatch.setattr(
        module, "_preflight_launch", lambda *args: SimpleNamespace(error=None),
    )
    monkeypatch.setattr(
        tracking, "save_record",
        lambda *args, **kwargs: pytest.fail("late authority rejection saved the foreign record"),
    )
    args = SimpleNamespace(json=True, base=False, dry_run=False, bare_resume=False)
    if json_mode:
        reads = iter([record, fresh])
        monkeypatch.setattr(tracking, "load_record", lambda *args: next(reads))
        monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
        monkeypatch.setattr(module, "_relocate_active_project_for_worktree", lambda *args: False)
        monkeypatch.setattr(module.worktree_identity, "_resolve_worktree_id", lambda value: value)
        monkeypatch.setattr(module, "_validate_profile_assignment_config", lambda *args: None)
        monkeypatch.setattr(module.local_cache_refresh, "prepare_for_launch", lambda *args, **kw: None)
        monkeypatch.setattr(module.sessions, "verify_worktree_active", lambda *args: None)
        state = module.ResolveCommandState(
            args=args, use_json=True, use_base=False, use_new=False,
            requested_machine=None, worktree_id=record.worktree_id, config=scoped_config,
        )
        assert module._resolve_json_mode(state) == 3
        assert "cross-space" in json.loads(capfd.readouterr().out)["error"]
    else:
        monkeypatch.setattr(tracking, "load_record", lambda *args: fresh)
        monkeypatch.setattr(module, "_dispatch_validate_profile_assignment_config", lambda *args: None)
        plans = []
        monkeypatch.setattr(module, "_emit_plan", plans.append)
        assert module._resolve_resume_context(module.ResolveLaunchContext(
            config=scoped_config, args=args, record=record,
        )) == 3
        assert plans[-1]["action"] == "error"
        assert plans[-1]["exit_code"] == 3
        assert "cross-space" in plans[-1]["error"]
    assert record.yaml_path.read_bytes() == before
