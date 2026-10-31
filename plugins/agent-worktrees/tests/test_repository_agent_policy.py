"""Repository vetoes precede adoption, seed writes and native launch effects."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from repository_policy import AgentHostingDenied

from agent_worktrees import __main__ as core
from agent_worktrees import agent_policy, bridge_acquisition_launch, embody_completion, handoff_cli
from agent_worktrees import installation_cli, installer, launch_request, launch_seed_exec, launch_seed_state
from agent_worktrees import maintenance_cli, registry_paths, resolve_cli, resolve_launch_cli
from agent_worktrees import tracking, worktree_creation, worktree_ops_cli
from test_resolve_seed_delivery import _args, _stub_launch_plumbing
from test_worktree_creation_seed import _create_config, _stub_create_worktree_core_internals


def _policy(root: Path, value: str = "false") -> Path:
    path = root / ".copilot-extensions" / "agent-worktrees" / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"expose_agent: {value}\n", encoding="utf-8")
    return path


def _unexpected(*args: object, **kwargs: object) -> None:
    pytest.fail("denied hosting reached an effectful boundary")


@pytest.mark.parametrize("argv", [["embody", "--new"], ["resolve", "--base", "--json"]])
def test_launch_parsers_have_no_hosting_opt_in(argv: list[str]) -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    handoff_cli.add_parsers(sub)
    resolve_cli.add_parsers(sub)
    args = parser.parse_args(argv)
    assert not hasattr(args, "require_agent_hosting")
    with pytest.raises(SystemExit) as exc:
        parser.parse_args([*argv, "--require-agent-hosting"])
    assert exc.value.code == 2


@pytest.fixture(autouse=True)
def _neutral_installation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry_paths, "installation_context", lambda: None)


@pytest.mark.parametrize("value", ["false", "invalid"])
def test_check_denied_or_invalid_is_json_without_project_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, value: str,
) -> None:
    _policy(tmp_path, value)
    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
    monkeypatch.setattr(core, "_extract_project_flag", _unexpected)
    monkeypatch.setattr(core.cfg, "load_config", _unexpected)
    assert core.main(["agent-policy", "check", "--path", str(tmp_path), "--json"]) == 3
    result = json.loads(capsys.readouterr().out)
    assert result["version"] == 1
    assert result["code"] == (
        "repository-agent-exposure-denied" if value == "false"
        else "repository-agent-exposure-invalid"
    )
    assert "error" in result and "allowed" not in result
    assert sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")) == before


@pytest.mark.parametrize("value", [None, "true"])
def test_check_unadopted_repo_only_reports_negative_eligibility(
    tmp_path: Path, capsys: pytest.CaptureFixture, value: str | None,
) -> None:
    if value:
        _policy(tmp_path, value)
    assert agent_policy.main(["check", "--path", str(tmp_path), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "version": 1, "repository": str(tmp_path.resolve()),
        "expose_agent": True if value else None,
        "sources": [str(tmp_path / ".copilot-extensions/agent-worktrees/config.yaml")] if value else [],
        "denied_by": [], "allowed": True,
    }
    assert not (tmp_path / ".agent-worktrees").exists()


@pytest.mark.parametrize("prefix", [["--project", "demo-repo"], ["-p", "demo-repo"], ["--project=demo-repo"]])
@pytest.mark.parametrize("value", [None, "false", "invalid"])
def test_project_policy_uses_read_only_owner_metadata_before_launch_planning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    prefix: list[str], value: str | None,
) -> None:
    config = _create_config(tmp_path)
    anchor = Path(config.default_repo.anchor)
    if value is not None:
        _policy(anchor, value)
    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    monkeypatch.setattr(core.cfg, "load_config", _unexpected)
    monkeypatch.setattr(resolve_cli, "cmd_resolve", _unexpected)
    assert core.main([*prefix, "agent-policy", "check", "--project-root", "--json"]) == (
        0 if value is None else 3
    )
    result = json.loads(capsys.readouterr().out)
    if value is None:
        assert result["allowed"] is True and result["repository"] == str(anchor)
    else:
        assert "allowed" not in result and "expose_agent" in result["error"]
    assert sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*")) == before


def test_owner_prefix_does_not_replace_an_explicit_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    _create_config(tmp_path)
    actual = tmp_path / "execution"
    actual.mkdir()
    _policy(actual)
    monkeypatch.setattr(core.cfg, "load_config", _unexpected)
    assert core.main([
        "--project", "demo-repo", "agent-policy", "check", "--path", str(actual), "--json",
    ]) == 3
    assert str(actual) in json.loads(capsys.readouterr().out)["error"]


@pytest.mark.parametrize("anchor", [".", "relative"])
def test_project_policy_refuses_cwd_relative_owner_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    anchor: str,
) -> None:
    config = _create_config(tmp_path)
    path = core.cfg.project_dir(config.repo_name) / "config.yaml"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["repos"][config.repo_name]["anchor"] = anchor
    path.write_text(json.dumps(data), encoding="utf-8")
    controller = tmp_path / "unrelated-controller"
    (controller / "relative").mkdir(parents=True)
    monkeypatch.chdir(controller)
    monkeypatch.setattr(agent_policy, "require_agent_hosting", _unexpected)
    assert core.main([
        "--project", config.repo_name, "agent-policy", "check", "--project-root", "--json",
    ]) == 3
    result = json.loads(capsys.readouterr().out)
    assert "must be absolute" in result["error"] and "allowed" not in result


def test_project_policy_does_not_reinterpret_unrelated_source_metadata(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    config = _create_config(tmp_path)
    path = core.cfg.project_dir(config.repo_name) / "config.yaml"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["repos"]["other-source"] = {"anchor": "relative-source"}
    path.write_text(json.dumps(data), encoding="utf-8")
    assert core.main([
        "--project", config.repo_name, "agent-policy", "check", "--project-root", "--json",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["repository"] == config.default_repo.anchor


def test_exact_worktree_policy_reads_record_and_restores_project_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    config = _create_config(tmp_path)
    actual = tmp_path / "execution"
    actual.mkdir()
    _policy(actual)
    records = tmp_path / "records"
    tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(actual), config.repo_name, "test", config.platform, records,
    )
    monkeypatch.setattr(core.cfg, "tracking_dir", lambda _project=None: records)
    monkeypatch.setattr(core.cfg, "load_config", _unexpected)
    previous = core.cfg.active_project()
    assert core.main([
        "--project", "demo-repo", "agent-policy", "check", "--worktree-id", "wt-a", "--json",
    ]) == 3
    assert str(actual) in json.loads(capsys.readouterr().out)["error"]
    assert core.cfg.active_project() == previous


@pytest.mark.parametrize("selection", [["--project-root"], ["--worktree-id", "wt-a"]])
def test_owner_metadata_selector_requires_an_explicit_project(
    selection: list[str], capsys: pytest.CaptureFixture,
) -> None:
    assert agent_policy.main(["check", *selection, "--json"]) == 3
    result = json.loads(capsys.readouterr().out)
    assert "explicit --project owner" in result["error"] and "allowed" not in result


def test_explicit_preflight_false_beats_stale_local_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _create_config(tmp_path)
    _policy(Path(config.default_repo.anchor))
    # This models the stale machine-local merged config; admission reads the
    # repository file, not this field or the explicitly selected launch profile.
    (core.cfg.project_dir(config.repo_name) / "config.yaml").write_text(
        "expose_agent: true\n", encoding="utf-8",
    )
    monkeypatch.setattr(resolve_launch_cli, "_launch_profile_selection", _unexpected)
    result = resolve_launch_cli.preflight_launch(
        config, argparse.Namespace(profile="explicit", recovery=True), config.default_repo.anchor,
    )
    assert "expose_agent: false" in result.error


@pytest.mark.parametrize("boundary", ["register", "install", "entry", "writer"])
def test_adoption_refuses_before_state_binstub_or_registry_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    (tmp_path / ".git").mkdir()
    _policy(tmp_path)
    monkeypatch.setattr(installer, "project_binstub_registration", _unexpected)
    monkeypatch.setattr(installer, "read_projects_registry", _unexpected)
    monkeypatch.setattr(installer.pr_authority, "guard", _unexpected)
    monkeypatch.setattr(core, "_prepare_namespaced_project_state", _unexpected)
    if boundary == "writer":
        with pytest.raises(AgentHostingDenied):
            installer.register_project("demo", repo_dir=tmp_path, expose_agent=True)
    elif boundary == "entry":
        assert maintenance_cli.cmd_register_project_entry(
            argparse.Namespace(project="demo", repo_dir=str(tmp_path), expose_agent=True),
        ) == 1
    elif boundary == "install":
        monkeypatch.setattr(installation_cli.cfg, "project_name", _unexpected)
        monkeypatch.setattr(installer, "check_prereqs", lambda: [])
        monkeypatch.setattr(installation_cli.worktree_identity, "_find_repo_dir", lambda: tmp_path)
        assert installation_cli.cmd_install(argparse.Namespace(machine=None)) == 1
    else:
        assert installation_cli.cmd_register(
            argparse.Namespace(project_name="demo", repo_dir=str(tmp_path), agent=True, machine=None),
        ) == 1


@pytest.mark.parametrize("surface", ["json", "interactive"])
def test_resume_refuses_before_seed_staging_or_liveness_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, surface: str,
) -> None:
    config = _create_config(tmp_path)
    tree = tmp_path / "tree"
    tree.mkdir()
    _policy(tree)
    records = tmp_path / "records"
    record = tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(tree), config.repo_name, "test", config.platform, records,
    )
    _stub_launch_plumbing(monkeypatch, config)
    monkeypatch.setattr(core, "_preflight_launch", resolve_launch_cli.preflight_launch)
    monkeypatch.setattr(core.cfg, "tracking_dir", lambda: records)
    monkeypatch.setattr(resolve_cli, "seed_for_attempt", _unexpected)
    monkeypatch.setattr(resolve_launch_cli, "seed_for_attempt", _unexpected)
    monkeypatch.setattr(core.sessions, "verify_worktree_active", _unexpected)
    args = _args(seed="must remain unsubmitted", dry_run=False, profile=None)
    if surface == "json":
        assert resolve_cli.cmd_resolve(args) == 3
    else:
        assert resolve_launch_cli._resolve_resume_context(
            resolve_launch_cli.ResolveLaunchContext(config=config, args=args, record=record),
        ) == 3


def test_new_launch_refuses_before_allocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _create_config(tmp_path)
    _policy(Path(config.default_repo.anchor))
    monkeypatch.setattr(core.git_ops, "create_worktree", _unexpected)
    with pytest.raises(core.LaunchPreflightError, match="expose_agent: false"):
        worktree_creation._create_worktree_core(config, pending_seed="new task")
    assert not (tmp_path / "worktrees").exists()


def test_plain_source_create_does_not_build_launch_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _create_config(tmp_path)
    _policy(Path(config.default_repo.anchor))
    _stub_create_worktree_core_internals(monkeypatch, tmp_path, config)
    monkeypatch.setattr(core.cfg, "tracking_dir", lambda: tmp_path / "records")
    monkeypatch.setattr(core, "_preflight_launch", _unexpected)
    monkeypatch.setattr(core, "_build_launch_cmd", _unexpected)
    monkeypatch.setattr(core.permissions, "clone_permissions", _unexpected)
    monkeypatch.setattr(core.permissions, "ensure_trusted_folder", _unexpected)
    monkeypatch.setattr(core.permissions, "ensure_extension_permission_approvals", _unexpected)
    monkeypatch.setattr(worktree_creation.local_cache_refresh, "prepare_for_launch", _unexpected)
    result = worktree_creation._create_worktree_core(
        config, build_launch_plan=False, inherit_parent_session=False,
    )
    assert result["worktree"]["id"]
    assert "launch" not in result


def test_create_command_selects_allocation_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}
    monkeypatch.setattr(worktree_ops_cli.project_state, "namespaced", lambda: False)
    monkeypatch.setattr(worktree_ops_cli.cfg, "load_config", lambda: object())

    def create(config: object, **kwargs: object) -> dict:
        captured.update(kwargs)
        return {"worktree": {"id": "source", "path": "/source"}}

    monkeypatch.setattr(worktree_ops_cli, "_create_worktree_core", create)
    assert worktree_ops_cli.cmd_create(
        argparse.Namespace(json=True, no_owner=True),
    ) == 0
    assert captured["build_launch_plan"] is False


@pytest.mark.parametrize("boundary", ["execute", "receiver"])
def test_remote_admission_reloads_policy_before_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    config = _create_config(tmp_path)
    anchor = Path(config.default_repo.anchor)
    agent_policy.require(anchor)
    _policy(anchor)
    directory = tmp_path / "untouched-records"
    monkeypatch.setattr(launch_request, "_dispatch", _unexpected)
    monkeypatch.setattr(launch_request.cfg, "tracking_dir", lambda: directory)
    monkeypatch.setattr(launch_request, "project_record_config", lambda record: config)
    with pytest.raises(AgentHostingDenied):
        if boundary == "receiver":
            launch_request._apply_admit({
                "project": config.repo_name, "kind": "new",
                "tracking_dir": str(directory), "request_id": "a" * 32,
            })
        else:
            launch_request.execute(config, {"kind": "new"})
    assert not directory.exists()


@pytest.mark.parametrize("boundary", ["deferred", "native", "final"])
def test_fresh_execution_veto_precedes_seed_lock_and_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    tree = tmp_path / "tree"
    tree.mkdir()
    agent_policy.require(tree)
    _policy(tree)
    record = SimpleNamespace(worktree_path=str(tree))
    monkeypatch.setattr(tracking, "load_record", lambda path: record)
    monkeypatch.setattr(launch_seed_exec.launch_admission, "guard", _unexpected)
    monkeypatch.setattr(launch_seed_exec.launch_seed_state, "peek", _unexpected)
    monkeypatch.setattr(launch_seed_exec.subprocess, "Popen", _unexpected)
    path = tmp_path / "record.yaml"
    if boundary == "deferred":
        assert launch_seed_exec.launch(path, ["copilot"], admission_only=True) == 3
    elif boundary == "final":
        assert launch_seed_exec._launch(path, ["copilot"], invoke=True, seed_id=None) == 3
    else:
        with pytest.raises(AgentHostingDenied), bridge_acquisition_launch.launch_fence(None, path):
            pytest.fail("native launch admitted")


def test_existing_live_embodiment_refuses_before_mux_probe_or_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _create_config(tmp_path)
    tree = tmp_path / "tree"
    tree.mkdir()
    _policy(tree)
    records = tmp_path / "records"
    tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(tree), config.repo_name, "test", config.platform, records,
    )
    monkeypatch.setattr(handoff_cli.cfg, "load_config", lambda: config)
    monkeypatch.setattr(handoff_cli.cfg, "tracking_dir", lambda: records)
    monkeypatch.setattr(handoff_cli.worktree_identity, "_resolve_worktree_id", lambda raw: raw)
    monkeypatch.setattr(handoff_cli.sessions, "has_mux_session", _unexpected)
    assert handoff_cli.cmd_embody(
        argparse.Namespace(worktree_id="wt-a", seed="do not submit"),
    ) == 3


def test_remote_receiver_requires_policy_capable_daemon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def dispatch(verb: str, args: dict, *, min_version: int) -> dict:
        assert min_version == 8
        raise ValueError("receiver predates repository hosting admission")

    monkeypatch.setattr(launch_request.launch_seed_state, "_dispatch", dispatch)
    with pytest.raises(ValueError, match="predates repository hosting"):
        launch_request._dispatch("launch_request_admit", {})


@pytest.mark.parametrize("operation", ["stage", "take"])
def test_seed_writer_freshness_precedes_lock_and_preserves_existing_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    tree = tmp_path / "tree"
    tree.mkdir()
    agent_policy.require(tree)
    _policy(tree)
    monkeypatch.setattr(tracking, "load_record", lambda path: SimpleNamespace(worktree_path=str(tree)))
    monkeypatch.setattr(launch_seed_state.launch_admission, "guard", _unexpected)
    handler = launch_seed_state._apply_stage if operation == "stage" else launch_seed_state._apply_take
    args = {"yaml_path": str(tmp_path / "record.yaml")}
    with pytest.raises(AgentHostingDenied):
        handler(args)
    assert launch_seed_state._authority_checked(handler)(args)["error"] == "repository-agent-exposure-denied"
    assert not (tmp_path / "record.yaml").exists()


def test_marketplace_true_cannot_erase_base_denial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _policy(tmp_path)
    overlay = tmp_path / ".copilot-extensions/agent-worktrees/marketplaces/selected/config.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text("expose_agent: true\n", encoding="utf-8")
    monkeypatch.setattr(registry_paths, "installation_context", lambda: {"marketplaceId": "selected"})
    with pytest.raises(AgentHostingDenied):
        agent_policy.require(tmp_path)


@pytest.mark.parametrize("context", [
    {}, [], {"marketplaceId": None}, {"marketplaceId": ""}, {"marketplaceId": 17},
    {"marketplaceId": " selected "}, {"marketplaceId": "invalid/identity"},
])
def test_invalid_receiving_context_never_falls_back_to_base_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture, context: object,
) -> None:
    monkeypatch.setattr(registry_paths, "installation_context", lambda: context)
    monkeypatch.setattr(core.cfg, "load_config", _unexpected)
    before = list(tmp_path.iterdir())
    assert agent_policy.main(["check", "--path", str(tmp_path), "--json"]) == 3
    result = json.loads(capsys.readouterr().out)
    assert result["code"] == "repository-agent-exposure-invalid"
    assert result["version"] == 1 and result["error"]
    assert "allowed" not in result
    assert list(tmp_path.iterdir()) == before


def test_context_validation_error_is_visible_without_policy_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    def invalid_context() -> dict:
        raise ValueError("selected installation context has the wrong owner")

    monkeypatch.setattr(registry_paths, "installation_context", invalid_context)
    assert agent_policy.main(["check", "--path", str(tmp_path), "--json"]) == 3
    result = json.loads(capsys.readouterr().out)
    assert result["code"] == "repository-agent-exposure-invalid"
    assert "wrong owner" in result["error"]
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("boundary", ["admit", "seed"])
def test_receiver_checks_selected_marketplace_on_actual_execution_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    tree = tmp_path / "actual"
    tree.mkdir()
    other = tmp_path / "unrelated"
    other.mkdir()
    monkeypatch.chdir(other)
    overlay = tree / ".copilot-extensions/agent-worktrees/marketplaces/receiving/config.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text("expose_agent: false\n", encoding="utf-8")
    monkeypatch.setattr(
        registry_paths, "installation_context", lambda: {"marketplaceId": "receiving"},
    )
    monkeypatch.setattr(tracking, "load_record", lambda path: SimpleNamespace(worktree_path=str(tree)))
    monkeypatch.setattr(tracking, "_RecordLock", _unexpected)
    monkeypatch.setattr(launch_seed_state.launch_admission, "guard", _unexpected)
    record_path = tmp_path / "wt-a.yaml"
    with pytest.raises(AgentHostingDenied):
        if boundary == "admit":
            launch_request._apply_admit({
                "kind": "resume", "tracking_dir": str(tmp_path), "worktree_id": "wt-a",
            })
        else:
            launch_seed_state._apply_stage({"yaml_path": str(record_path)})
    assert not record_path.exists()
    assert not (tmp_path / "launch-requests").exists()


def test_missing_execution_checkout_does_not_fall_back_to_allowed_anchor(
    tmp_path: Path,
) -> None:
    config = _create_config(tmp_path)
    result = resolve_launch_cli.preflight_launch(
        config, argparse.Namespace(recovery=False), str(tmp_path / "missing"),
    )
    assert result.error and "Cannot resolve execution workspace" in result.error


def test_post_spawn_policy_change_blocks_seed_without_terminating_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _policy(tmp_path)
    monkeypatch.setattr(launch_seed_state, "claim_creation", _unexpected)
    assert embody_completion.complete_launch(
        argparse.Namespace(), wt_id="wt-a", work_dir=str(tmp_path), record=None,
        acquisition=None, deferred={}, explicit_seed=True, seed="must not submit",
        prepared_context=None, resume_target=None, driver=None, selection=None,
        result={"new_pane": "pane"}, cfg=None, sessions=None, output=core.output,
        launch_seed_state=launch_seed_state, tracking_write=None,
        profile_assignment=None, clock=None,
    ) == 3


def test_registration_admits_before_transaction_and_keeps_write_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = []
    original = installer.require_project_hosting

    def admit(project: str, repo_dir: Path | str | None = None) -> None:
        events.append("admit")
        original(project, repo_dir)

    @contextmanager
    def guard() -> Iterator[None]:
        events.append("lock")
        try:
            yield
        finally:
            events.append("unlock")

    def read() -> dict:
        events.append("read")
        return {"projects": {}}

    def write(registry: dict) -> None:
        assert registry["projects"]["demo"]["expose_agent"] is True
        events.append("write")

    monkeypatch.setattr(installer, "require_project_hosting", admit)
    monkeypatch.setattr(installer.pr_authority, "guard", guard)
    monkeypatch.setattr(installer, "read_projects_registry", read)
    monkeypatch.setattr(installer, "write_projects_registry", write)
    monkeypatch.setattr(installer.project_state, "namespaced", lambda: False)
    installer.register_project("demo", repo_dir=tmp_path, expose_agent=True)
    assert events == ["admit", "lock", "admit", "read", "write", "unlock"]
