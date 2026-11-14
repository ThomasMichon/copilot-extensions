"""Launch config-root validation must precede every launch-side mutation."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent_worktrees import __main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import state_root
from agent_worktrees import worktree_identity


def _config(tmp_path) -> cfg.Config:
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    return cfg.Config(
        srcroot=str(tmp_path),
        machine="test",
        platform="windows" if __import__("os").name == "nt" else "linux",
        repo_name="demo",
        repos={
            "demo": cfg.RepoConfig(
                anchor=str(anchor),
                worktree_root=str(tmp_path / "worktrees"),
                setup_hook={
                    "windows": "setup.ps1",
                    "linux": "setup.sh",
                },
            )
        },
    )


def _unsafe_root(config, *, cwd=None, project=None):
    return state_root.ConfigRoot(
        None,
        "machine_local",
        config.repo_name,
        True,
        False,
        error="machine-local config root is unsafe",
    )


def test_launch_preflight_uses_active_project_for_config_root(
    tmp_path,
    monkeypatch,
):
    base = _config(tmp_path)
    config = cfg.Config(
        srcroot=base.srcroot,
        machine=base.machine,
        platform=base.platform,
        repo_name="selected-repo",
        repos={"selected-repo": base.repos["demo"]},
    )
    captured = {}
    monkeypatch.setattr(m.cfg, "active_project", lambda: "control-project")

    def resolve(config_arg, *, cwd=None, project=None):
        captured.update(config=config_arg, cwd=cwd, project=project)
        return state_root.ConfigRoot(
            str(tmp_path / ".control-project"),
            "machine_local",
            config_arg.repo_name,
            False,
            True,
        )

    monkeypatch.setattr(m.state_root_mod, "resolve_config_root", resolve)
    args = argparse.Namespace(recovery=False)

    preflight = m._preflight_launch(
        config, args, str(tmp_path / "future-worktree"),
        allocation_source=config.default_repo.anchor,
    )

    assert preflight.error is None
    assert captured["project"] == "control-project"
    assert captured["config"].repo_name == "selected-repo"


def _create_args(
    *,
    json_output: bool,
    system: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        system=system,
        no_owner=True,
        owner_ref=None,
        owner=None,
        name="service" if system else None,
        interface=None,
        origin=None,
        json=json_output,
    )


def test_new_authoring_refuses_required_knowledge_before_allocation(
    tmp_path, monkeypatch, capfd,
):
    config = replace(_config(tmp_path), knowledge_repo="knowledge")
    monkeypatch.setattr(m.cfg, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(
        state_root, "resolve_state_root",
        lambda *_a, **_k: state_root.StateRoot(
            None, "knowledge_repo", config.knowledge_repo or "", True, True, False,
            error="Required knowledge repository is not available",
        ),
    )
    _forbid_create_mutations(monkeypatch)

    rc = m.cmd_resolve(m.build_parser().parse_args(["resolve", "--json", "--new"]))

    captured = capfd.readouterr()
    assert rc != 0
    assert "knowledge" in json.loads(captured.out)["error"].lower()
    assert "Traceback" not in captured.out + captured.err
    assert not (tmp_path / "worktrees").exists()


@pytest.mark.parametrize("requirement", ["stateless", "requires_external_state_root"])
def test_unbound_new_keeps_storage_and_coordination_unavailable(
    tmp_path, monkeypatch, requirement,
):
    from agent_worktrees import application_admission, launch_preflight

    base = _config(tmp_path)
    config = replace(base, repos={
        base.repo_name: replace(base.default_repo, **{requirement: True}),
    })
    checked = launch_preflight.LaunchPreflight(checked_profile="selected-profile")
    observed = []

    def applications(config_arg, *, source):
        observed.append((config_arg, source))
        return checked

    monkeypatch.setattr(application_admission, "new_application_preflight", applications)
    result = launch_preflight.new_authoring_preflight(
        config, source=config.default_repo.anchor,
    )

    assert result is checked
    assert observed == [(config, config.default_repo.anchor)]
    root = state_root.resolve_state_root(config)
    assert root.requires_external and not root.bound and root.path is None
    readiness = state_root.coordination_readiness(config)
    assert not readiness.ready and readiness.code == "knowledge_binding_required"


def test_unbound_new_still_refuses_unready_required_applications(
    tmp_path, monkeypatch, capfd,
):
    from agent_worktrees import application_admission

    base = _config(tmp_path)
    config = replace(base, repos={
        base.repo_name: replace(
            base.default_repo, stateless=True, require_application_readiness=True,
        ),
    })
    monkeypatch.setattr(m.cfg, "load_config", lambda *a, **k: config)

    def unavailable(**kwargs):
        raise ValueError("owner unavailable")

    monkeypatch.setattr(application_admission, "_selected_runtime", unavailable)
    _forbid_create_mutations(monkeypatch)

    rc = m.cmd_resolve(m.build_parser().parse_args(["resolve", "--json", "--new"]))

    assert rc != 0
    error = json.loads(capfd.readouterr().out)["error"]
    assert "requires application readiness" in error
    assert not (tmp_path / "worktrees").exists()


@pytest.mark.parametrize("arguments", [
    ["coordination-readiness"], ["resolve", "--json", "--new"],
    ["resolve", "--new", "--no-mux"],
])
def test_unresolved_project_configuration_has_a_controlled_admission_error(
    monkeypatch, capfd, arguments,
):
    from configuration_layers import ConfigurationError

    def unavailable(*_a, **_k):
        raise ConfigurationError("explicit knowledge binding is unresolved")

    monkeypatch.setattr(m.cfg, "load_config", unavailable)
    if arguments[0] == "coordination-readiness":
        result = m.cmd_coordination_readiness_dispatch([])
    else:
        result = m.cmd_resolve(m.build_parser().parse_args(arguments))
    assert result != 0
    captured = capfd.readouterr()
    if arguments[0] == "coordination-readiness" or "--json" in arguments:
        payload = json.loads(captured.out)
        assert payload["error"] == "explicit knowledge binding is unresolved"
        assert payload.get("ready") is not True
    else:
        assert "explicit knowledge binding is unresolved" in captured.out + captured.err


@pytest.mark.parametrize("external", [False, True], ids=["self-hosted", "bound-knowledge"])
def test_new_authoring_reads_native_readiness_in_the_allocation_source(
    tmp_path, monkeypatch, external,
):
    config = _config(tmp_path)
    observed = []

    def resolve(config_arg, *, cwd=None):
        observed.append((config_arg, cwd))
        return state_root.StateRoot(
            str(tmp_path), "knowledge_repo" if external else "launch_repo",
            "knowledge" if external else config.repo_name,
            external, external, True,
        )

    monkeypatch.setattr(state_root, "resolve_state_root", resolve)
    monkeypatch.setattr(
        state_root, "resolve_config_root",
        lambda *_a, **_k: state_root.ConfigRoot(
            str(tmp_path / "local-config"), "machine_local", config.repo_name, external, True,
        ),
    )

    result = m._preflight_launch(
        config, argparse.Namespace(recovery=False), str(tmp_path / "future-worktree"),
        allocation_source=config.default_repo.anchor,
    )

    assert result.error is None
    assert observed == [(config, config.default_repo.anchor)]


@pytest.mark.parametrize("recovery", [False, True], ids=["existing-session", "recovery"])
def test_existing_session_and_recovery_keep_their_knowledge_admission(
    tmp_path, monkeypatch, recovery,
):
    config = _config(tmp_path)
    worktree = tmp_path / "existing-worktree"
    worktree.mkdir()
    monkeypatch.setattr(
        state_root, "coordination_readiness",
        lambda *_a, **_k: pytest.fail("Existing-session/recovery preflight acquired a New gate"),
    )
    monkeypatch.setattr(
        state_root, "resolve_config_root",
        lambda *_a, **_k: state_root.ConfigRoot(
            str(tmp_path / "local-config"), "machine_local", config.repo_name, False, True,
        ),
    )

    result = m._preflight_launch(
        config, argparse.Namespace(recovery=recovery), str(worktree),
        **({"allocation_source": config.default_repo.anchor} if recovery else {}),
    )

    assert result.error is None


def _forbid_create_mutations(monkeypatch) -> None:
    def forbidden(*_args, **_kwargs):
        pytest.fail("launch preflight failure reached a create mutation")

    monkeypatch.setattr(m.git_ops, "git", forbidden)
    monkeypatch.setattr(m.git_ops, "prepare_worktree_base", forbidden)
    monkeypatch.setattr(m.git_ops, "create_worktree", forbidden)
    monkeypatch.setattr(m.tracking, "create_new_record", forbidden)
    monkeypatch.setattr(m.permissions, "clone_permissions", forbidden)
    monkeypatch.setattr(m.permissions, "ensure_trusted_folder", forbidden)


def _stub_successful_create(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        m.git_ops,
        "git",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(m.git_ops, "resolve_start_point", lambda *_a, **_k: "HEAD")
    monkeypatch.setattr(
        m.git_ops,
        "prepare_worktree_base",
        lambda *_a, **_k: SimpleNamespace(
            start_point="HEAD",
            fetched=False,
            fetch_error=None,
            anchor=SimpleNamespace(
                updated=False,
                reason="no-upstream",
                behind=0,
            ),
        ),
    )
    monkeypatch.setattr(m.git_ops, "create_worktree", lambda *_a, **_k: None)
    monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tmp_path / "tracking")
    monkeypatch.setattr(
        m.tracking,
        "create_new_record",
        lambda **kwargs: SimpleNamespace(receiving_allocation_present=False, **kwargs),
    )
    monkeypatch.setattr(m.permissions, "clone_permissions", lambda *_a: False)
    monkeypatch.setattr(m.permissions, "ensure_trusted_folder", lambda *_a: False)
    monkeypatch.setattr(m.activity, "log_event", lambda *_a, **_k: None)
    monkeypatch.setattr(
        m,
        "_worktree_to_dict",
        lambda record: {
            "id": record.worktree_id,
            "path": record.worktree_path,
            "branch": record.branch,
            "kind": record.kind,
        },
    )


@pytest.mark.parametrize("json_output", [False, True])
def test_create_preflight_failure_has_zero_mutations_and_no_traceback(
    tmp_path,
    monkeypatch,
    capfd,
    json_output,
):
    """Seeded creation prepares authoring; plain source allocation does not."""
    config = _config(tmp_path)
    monkeypatch.setattr(m.cfg, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(m.state_root_mod, "resolve_config_root", _unsafe_root)
    _forbid_create_mutations(monkeypatch)

    args = _create_args(json_output=json_output)
    args.seed = "New authoring intent requiring launch preparation"
    rc = m.cmd_create(args)

    captured = capfd.readouterr()
    assert rc == 1
    assert not (tmp_path / "worktrees").exists()
    assert "Traceback" not in captured.out + captured.err
    if json_output:
        assert json.loads(captured.out)["error"] == "machine-local config root is unsafe"
    else:
        assert "machine-local config root is unsafe" in captured.err


@pytest.mark.parametrize("json_output", [False, True])
def test_system_create_skips_unsafe_launch_preflight_and_emits_path_only(
    tmp_path,
    monkeypatch,
    capfd,
    json_output,
):
    config = _config(tmp_path)
    monkeypatch.setattr(m.cfg, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(
        m,
        "_preflight_launch",
        lambda *_a, **_k: pytest.fail("system create ran launch preflight"),
    )
    monkeypatch.setattr(
        m,
        "_build_launch_cmd",
        lambda *_a, **_k: pytest.fail("system create built a launch plan"),
    )
    _stub_successful_create(monkeypatch, tmp_path)

    rc = m.cmd_create(_create_args(json_output=json_output, system=True))

    captured = capfd.readouterr()
    assert rc == 0, (captured.out, captured.err)
    assert "Traceback" not in captured.out + captured.err
    if json_output:
        data = json.loads(captured.out)
        assert set(data) == {"version", "worktree"}
        assert data["worktree"]["kind"] == "system"
        assert data["worktree"]["path"].startswith(str(tmp_path / "worktrees"))
        assert "launch" not in data
    else:
        assert "Created system worktree" in captured.out
        assert "Path:" in captured.out
        assert "launch" not in captured.out.lower()


def test_source_only_allocation_does_not_acquire_new_authoring_readiness(
    tmp_path, monkeypatch, capfd,
):
    base = _config(tmp_path)
    config = replace(base, repos={"demo": replace(base.default_repo, stateless=True)})
    monkeypatch.setattr(m.cfg, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(
        m, "_preflight_launch",
        lambda *_a, **_k: pytest.fail("Source-only allocation acquired authoring preflight"),
    )
    _stub_successful_create(monkeypatch, tmp_path)
    args = _create_args(json_output=True)
    args.no_pair = True

    assert m.cmd_create(args) == 0
    result = json.loads(capfd.readouterr().out)
    assert result["worktree"]["kind"] == "session"
    assert "launch" not in result


def test_resolve_json_new_preflight_failure_has_zero_mutations(
    tmp_path,
    monkeypatch,
    capfd,
):
    config = _config(tmp_path)
    monkeypatch.setattr(m.cfg, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(m.state_root_mod, "resolve_config_root", _unsafe_root)
    _forbid_create_mutations(monkeypatch)
    args = m.build_parser().parse_args(["resolve", "--json", "--new"])

    rc = m.cmd_resolve(args)

    captured = capfd.readouterr()
    assert rc == 1
    assert not (tmp_path / "worktrees").exists()
    assert "Traceback" not in captured.out + captured.err
    assert json.loads(captured.out)["error"] == "machine-local config root is unsafe"


def test_resolve_json_new_codename_policy_error_is_a_clean_json_error(
    tmp_path,
    monkeypatch,
    capfd,
):
    """PR #3037 review finding: `CodenameAttributionPolicyError` must reach
    `resolve --json --new`'s caller as a clean JSON error, not a raw
    traceback -- that CLI path's error handling only caught `RuntimeError`,
    which the new exception did not originally subclass.
    """
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    config = cfg.Config(
        srcroot=str(tmp_path),
        machine="test",
        platform="windows" if __import__("os").name == "nt" else "linux",
        repo_name="demo",
        repos={
            "demo": cfg.RepoConfig(
                anchor=str(anchor),
                worktree_root=str(tmp_path / "worktrees"),
                pr=cfg.PRConfig(enabled=True, source_attribution_configured=False),
                codename=cfg.CodenameConfig(
                    wordlist_path=str(tmp_path / "custom-wordlist.json"),
                    wordlist_path_configured=True,
                ),
            )
        },
    )
    monkeypatch.setattr(m.cfg, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(
        m,
        "_create_worktree_core",
        lambda *_a, **_k: (_ for _ in ()).throw(
            m.codename_tracking.CodenameAttributionPolicyError(
                "PR-active repo 'demo' has a custom codename wordlist "
                "configured but pr.source_attribution is not explicit"
            )
        ),
    )
    args = m.build_parser().parse_args(["resolve", "--json", "--new"])

    rc = m.cmd_resolve(args)

    captured = capfd.readouterr()
    assert rc == 1
    assert "Traceback" not in captured.out + captured.err
    assert "custom codename wordlist" in json.loads(captured.out)["error"]


def test_resolve_plain_new_codename_policy_error_is_a_clean_cli_error(
    tmp_path,
    monkeypatch,
    capfd,
):
    """Round-6 review finding: the non-JSON `resolve --new` route calls
    `_resolve_new` (-> `_create_worktree_core`) without catching
    `CodenameAttributionPolicyError`, and `main()`'s own top-level dispatch
    only normalized `FileNotFoundError`/`ValueError`/
    `BinstubOwnershipError` -- a real policy rejection from a plain,
    interactive invocation could terminate with a raw traceback instead of
    a clean CLI error.
    """
    config = _config(tmp_path)
    monkeypatch.setattr(m.cfg, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(
        m,
        "_resolve_new",
        lambda *_a, **_k: (_ for _ in ()).throw(
            m.codename_tracking.CodenameAttributionPolicyError(
                "PR-active repo 'demo' has a custom codename wordlist "
                "configured but pr.source_attribution is not explicit"
            )
        ),
    )
    args = m.build_parser().parse_args(["resolve", "--new", "--no-mux"])

    rc = m.cmd_resolve(args)

    captured = capfd.readouterr()
    assert rc == 1
    assert "Traceback" not in captured.out + captured.err
    assert "custom codename wordlist" in captured.err


def test_resolve_json_resume_preflight_failure_precedes_tracking_mutation(
    tmp_path,
    monkeypatch,
    capfd,
    sample_record,
):
    config = _config(tmp_path)
    tracking_dir = tmp_path / "tracking"
    tracking_dir.mkdir()
    (tracking_dir / "wt-1.yaml").write_text("placeholder\n", encoding="utf-8")
    record = replace(
        sample_record,
        worktree_id="wt-1",
        repo=config.repo_name,
        machine=config.machine,
        platform=config.platform,
        worktree_path=str(tmp_path / "worktrees" / "wt-1"),
    )
    (tmp_path / "worktrees" / "wt-1").mkdir(parents=True)
    monkeypatch.setattr(m.cfg, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tracking_dir)
    monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda _value: "wt-1")
    monkeypatch.setattr(m.tracking, "load_record", lambda _path: record)
    monkeypatch.setattr(m.state_root_mod, "resolve_config_root", _unsafe_root)
    monkeypatch.setattr(
        m.tracking,
        "_RecordLock",
        lambda *_args, **_kwargs: pytest.fail(
            "preflight failure reached tracking mutation"
        ),
    )
    args = m.build_parser().parse_args([
        "resolve",
        "--json",
        "--worktree-id",
        "wt-1",
    ])

    rc = m.cmd_resolve(args)

    captured = capfd.readouterr()
    assert rc == 3
    assert "Traceback" not in captured.out + captured.err
    assert json.loads(captured.out)["error"] == "machine-local config root is unsafe"


def test_interactive_resume_preflight_failure_precedes_all_mutations(
    tmp_path,
    monkeypatch,
    capfd,
    sample_record,
):
    config = _config(tmp_path)
    record = replace(
        sample_record,
        worktree_id="wt-1",
        repo=config.repo_name,
        machine=config.machine,
        platform=config.platform,
        worktree_path=str(tmp_path / "worktrees" / "wt-1"),
        branch="worktree/wt-1",
    )
    (tmp_path / "worktrees" / "wt-1").mkdir(parents=True)
    monkeypatch.setattr(m.state_root_mod, "resolve_config_root", _unsafe_root)
    monkeypatch.setattr(m.sessions, "verify_worktree_active", lambda _record: None)

    def forbidden(*_args, **_kwargs):
        pytest.fail("launch preflight failure reached a resume mutation")

    monkeypatch.setattr(m.tracking, "_RecordLock", forbidden)
    monkeypatch.setattr(m.tracking, "stamp_mux_live", forbidden)
    monkeypatch.setattr(m.tracking, "stamp_bound_live", forbidden)
    monkeypatch.setattr(m.git_ops, "fast_forward_worktree", forbidden)
    monkeypatch.setattr(m.activity, "log_event", forbidden)
    args = argparse.Namespace(
        recovery=False,
        dry_run=False,
        json=False,
        base=False,
        bare_resume=False,
        no_mux=False,
        no_fast_forward=False,
        copilot_args=[],
    )

    with m.output.stdout_to_stderr():
        rc = m._resolve_resume(record, config, args)

    captured = capfd.readouterr()
    assert rc == 3
    assert "Traceback" not in captured.out + captured.err
    assert json.loads(captured.out) == {
        "action": "error",
        "error": "machine-local config root is unsafe",
        "exit_code": 3,
    }


def test_base_launch_preflight_failure_is_controlled(
    tmp_path,
    monkeypatch,
    capfd,
):
    config = _config(tmp_path)
    monkeypatch.setattr(m.state_root_mod, "resolve_config_root", _unsafe_root)
    args = m.build_parser().parse_args(["resolve", "--base"])

    rc = m._resolve_base_repo(config, args)

    captured = capfd.readouterr()
    assert rc == 3
    assert "Traceback" not in captured.out + captured.err
    plan = json.loads(captured.out)
    assert plan == {
        "action": "error",
        "error": "machine-local config root is unsafe",
        "exit_code": 3,
    }


def test_recovery_launch_skips_config_root_preflight(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr(
        m.state_root_mod,
        "resolve_config_root",
        lambda *_args, **_kwargs: pytest.fail("recovery ran config-root preflight"),
    )
    args = argparse.Namespace(recovery=True, copilot_args=[])

    preflight = m._preflight_launch(config, args, config.default_repo.anchor)

    assert preflight.error is None
    assert preflight.config_root is None
