"""Execution-space identity must not collapse through a shared OS hostname."""

from contextlib import nullcontext
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
    return SimpleNamespace(
        machine="workstation-windows", repo_name="project",
        default_repo=SimpleNamespace(anchor=str(tmp_path)),
    )


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
    scoped_config.machine = f"workstation-{platform}"
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
        machine="workstation-windows", worktree_id="wt-child",
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


@pytest.mark.parametrize("machine", ["workstation", "workstation-wsl"])
def test_unowned_create_requires_the_current_registered_space(
    machine, tmp_path, scoped_config, monkeypatch,
):
    import agent_worktrees.__main__ as main

    anchor = tmp_path / "anchor"
    anchor.mkdir()
    config = cfg.Config(
        machine=machine, platform="windows", repo_name="project", srcroot=str(tmp_path),
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
        machine="workstation-windows", worktree_id="wt-child",
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
        machine="workstation-windows", worktree_id="wt-child",
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
