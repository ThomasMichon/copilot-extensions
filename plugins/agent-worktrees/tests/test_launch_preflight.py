"""Launch config-root validation must precede every launch-side mutation."""

from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

import pytest

from agent_worktrees import __main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import state_root


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

    preflight = m._preflight_launch(config, args, str(tmp_path / "future-worktree"))

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


def _forbid_create_mutations(monkeypatch) -> None:
    def forbidden(*_args, **_kwargs):
        pytest.fail("launch preflight failure reached a create mutation")

    monkeypatch.setattr(m.git_ops, "git", forbidden)
    monkeypatch.setattr(m.git_ops, "prepare_worktree_base", forbidden)
    monkeypatch.setattr(m.git_ops, "create_worktree", forbidden)
    monkeypatch.setattr(m.tracking, "create_new_record", forbidden)
    monkeypatch.setattr(m.permissions, "clone_permissions", forbidden)
    monkeypatch.setattr(m.permissions, "add_trusted_folder", forbidden)
    monkeypatch.setattr(m, "_reconcile_marketplaces_for_checkout", forbidden)


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
        lambda **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(m.permissions, "clone_permissions", lambda *_a: False)
    monkeypatch.setattr(m.permissions, "add_trusted_folder", lambda *_a: False)
    monkeypatch.setattr(m.activity, "log_event", lambda *_a, **_k: None)
    monkeypatch.setattr(
        m,
        "_reconcile_marketplaces_for_checkout",
        lambda *_a, **_k: None,
    )
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
    config = _config(tmp_path)
    monkeypatch.setattr(m.cfg, "load_config", lambda: config)
    monkeypatch.setattr(m.state_root_mod, "resolve_config_root", _unsafe_root)
    _forbid_create_mutations(monkeypatch)

    rc = m.cmd_create(_create_args(json_output=json_output))

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
    monkeypatch.setattr(m.cfg, "load_config", lambda: config)
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
    assert rc == 0
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


def test_resolve_json_new_preflight_failure_has_zero_mutations(
    tmp_path,
    monkeypatch,
    capfd,
):
    config = _config(tmp_path)
    monkeypatch.setattr(m.cfg, "load_config", lambda: config)
    monkeypatch.setattr(m.state_root_mod, "resolve_config_root", _unsafe_root)
    _forbid_create_mutations(monkeypatch)
    args = m.build_parser().parse_args(["resolve", "--json", "--new"])

    rc = m.cmd_resolve(args)

    captured = capfd.readouterr()
    assert rc == 1
    assert not (tmp_path / "worktrees").exists()
    assert "Traceback" not in captured.out + captured.err
    assert json.loads(captured.out)["error"] == "machine-local config root is unsafe"


def test_resolve_json_resume_preflight_failure_precedes_tracking_mutation(
    tmp_path,
    monkeypatch,
    capfd,
):
    config = _config(tmp_path)
    tracking_dir = tmp_path / "tracking"
    tracking_dir.mkdir()
    (tracking_dir / "wt-1.yaml").write_text("placeholder\n", encoding="utf-8")
    record = SimpleNamespace(worktree_path=str(tmp_path / "worktrees" / "wt-1"))
    monkeypatch.setattr(m.cfg, "load_config", lambda: config)
    monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tracking_dir)
    monkeypatch.setattr(m, "_resolve_worktree_id", lambda _value: "wt-1")
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
):
    config = _config(tmp_path)
    record = SimpleNamespace(
        worktree_id="wt-1",
        worktree_path=str(tmp_path / "worktrees" / "wt-1"),
        yaml_path=tmp_path / "tracking" / "wt-1.yaml",
        branch="worktree/wt-1",
    )
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
