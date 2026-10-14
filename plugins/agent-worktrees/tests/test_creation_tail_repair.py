"""Recovery restores the creation tail without taking the retained prompt."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import config as cfg, creation_tail_repair as repair
from agent_worktrees import git_ops, installer, launch_seed_state, local_cache_refresh
from agent_worktrees import maintenance_cli, permissions, repos, sessions, state_root
from agent_worktrees import status_monitor_runtime, tracking, worktree_creation


def _creation_fixture(tmp_path, monkeypatch, *, native_git=False):
    platform = cfg.detect_platform()
    short = "win" if platform == "windows" else platform
    identity = f"host-{short}-20261013-100000-abcd"
    anchors = {name: tmp_path / name for name in ("harness", "knowledge")}
    for anchor in anchors.values():
        anchor.mkdir()
        if native_git:
            git_ops.git("init", "-b", "main", cwd=anchor)
            git_ops.git(
                "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "fixture",
                cwd=anchor,
            )
            git_ops.git("remote", "add", "origin", str(anchor), cwd=anchor)
    roots = {name: Path(cfg.derive_worktree_root(str(anchor))) for name, anchor in anchors.items()}
    configs = {
        name: cfg.Config(
            srcroot=str(tmp_path), machine="host", platform=platform, repo_name=name,
            knowledge_repo="knowledge" if name == "harness" else "",
            auto_fast_forward=False,
            repos={name: cfg.RepoConfig(
                anchor=str(anchor), worktree_root=str(roots[name]), default_branch="main",
            )},
        )
        for name, anchor in anchors.items()
    }
    projects = {name: tmp_path / f".{name}" for name in anchors}
    for name, project in projects.items():
        project.mkdir()
        (project / "config.yaml").write_text(json.dumps({
            "machine": "host", "repo_name": name,
            "repos": {name: {
                "anchor": str(anchors[name]), "worktree_root": str(roots[name]),
                "default_branch": "main",
            }},
        }), encoding="utf-8")
    monkeypatch.setattr(cfg, "_home", lambda: tmp_path)
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: projects[name or "harness"])
    monkeypatch.setattr(
        cfg, "tracking_dir", lambda project=None: projects[project or "harness"] / "worktrees",
    )
    def load_config(config_file=None, *, project=None, **kwargs):
        name = project or (Path(config_file).parent.name.lstrip(".") if config_file else "harness")
        return configs[name]

    monkeypatch.setattr(cfg, "load_config", load_config)
    monkeypatch.setattr(
        installer, "read_projects_registry", lambda **kw: {"projects": {name: {} for name in anchors}},
    )
    entries = {
        name: repos.RepoEntry(
            name=name, repo_class="worktree", remote="origin", default_branch="main",
            paths={platform: str(anchor)},
        )
        for name, anchor in anchors.items()
    }
    monkeypatch.setattr(repos, "find_repo", entries.get)
    monkeypatch.setattr(status_monitor_runtime, "_status_monitor_enabled", lambda: False)
    monkeypatch.setattr(sessions, "verify_worktree_active", lambda record: sessions.LiveVerdict())
    root = state_root.StateRoot(
        str(anchors["knowledge"]), "knowledge_repo", "knowledge", True, True, True,
    )
    monkeypatch.setattr(state_root, "resolve_state_root", lambda config=None, **kw: root)
    copilot = tmp_path / ".copilot"
    copilot.mkdir()
    (copilot / "config.json").write_text('{"trustedFolders":[]}', encoding="utf-8")
    monkeypatch.setattr(permissions, "_copilot_dir", lambda: copilot)
    monkeypatch.setattr(
        local_cache_refresh, "prepare_for_launch",
        lambda path: local_cache_refresh.RefreshResult("ready"),
    )
    checkout = roots["harness"] / identity
    if native_git:
        git_ops.create_worktree(str(anchors["harness"]), str(checkout), f"worktree/{identity}", "HEAD")
    else:
        checkout.mkdir(parents=True)
        monkeypatch.setattr(repair, "_same_repository", lambda *args, **kw: None)
    record = tracking.create_new_record(
        identity, f"worktree/{identity}", str(checkout), "harness", "host", platform,
        projects["harness"] / "worktrees",
    )
    seed = launch_seed_state.stage(record.yaml_path, kind="new", text="retained\nprivate intent")
    return SimpleNamespace(
        record=record, seed=seed, configs=configs, anchors=anchors, roots=roots,
        projects=projects, root=root, copilot=copilot, short=short,
    )


@pytest.fixture
def creation(tmp_path, monkeypatch):
    return _creation_fixture(tmp_path, monkeypatch)


@pytest.fixture
def native_creation(tmp_path, monkeypatch):
    return _creation_fixture(tmp_path, monkeypatch, native_git=True)


def _args(creation, *, fix=False, **changes):
    values = {
        "repair_creation": creation.record.worktree_id, "seed_id": creation.seed.seed_id,
        "fix": fix, "json": True,
    }
    values.update(changes)
    return argparse.Namespace(**values)


def _run(creation, capsys, *, fix=False, **changes):
    with repair.output.capture_json_output() as buffer:
        result = repair.targeted_doctor(_args(creation, fix=fix, **changes))
    captured = capsys.readouterr()
    rendered = buffer.getvalue()
    assert creation.seed.text not in rendered + captured.out + captured.err
    return result, json.loads(rendered)


@pytest.mark.guard
def test_creation_report_is_scoped_read_only(creation, monkeypatch, capsys):
    before = creation.record.yaml_path.read_bytes()
    state_before = launch_seed_state.state_path(creation.record.yaml_path).read_bytes()
    for name in ("clone_permissions", "add_trusted_folder", "ensure_extension_permission_approvals"):
        monkeypatch.setattr(permissions, name, lambda *a: pytest.fail("report mutated permissions"))
    monkeypatch.setattr(
        worktree_creation, "_carve_paired_knowledge", lambda *a, **kw: pytest.fail("report allocated"),
    )
    code, report = _run(creation, capsys)
    assert code == 0 and report["repairable"] and not report["repaired"]
    assert report["checks"] == {"trusted": False, "facility_approvals": False, "paired": False}
    assert creation.record.yaml_path.read_bytes() == before
    assert launch_seed_state.state_path(creation.record.yaml_path).read_bytes() == state_before
    assert not creation.roots["knowledge"].exists()


@pytest.mark.guard
@pytest.mark.parametrize("change", [
    {"seed_id": None, "fix": True},
    {"seed_id": "different", "fix": True},
    {"gc_sessions": True},
    {"repair_record": "other"},
    {"repair_creation": "../other"},
])
def test_creation_refuses_unverified_scope_before_effects(creation, monkeypatch, capsys, change):
    monkeypatch.setattr(
        permissions, "clone_permissions", lambda *a: pytest.fail("refused operation mutated"),
    )
    code, report = _run(creation, capsys, **change)
    assert code == 1 and report["error"] and not report["repaired"]
    assert launch_seed_state.peek(creation.record.yaml_path) == creation.seed


@pytest.mark.guard
@pytest.mark.parametrize("verdict", [
    sessions.LiveVerdict(active=True),
    sessions.LiveVerdict(probes_ok=False),
    sessions.LiveVerdict(mux_probe_ok=False),
])
def test_creation_requires_confirmed_cold_workspace(creation, monkeypatch, capsys, verdict):
    monkeypatch.setattr(sessions, "verify_worktree_active", lambda record: verdict)
    monkeypatch.setattr(
        permissions, "clone_permissions", lambda *a: pytest.fail("live operation mutated"),
    )
    code, report = _run(creation, capsys, fix=True)
    assert code == 1 and "seed remains staged" in report["error"]
    assert launch_seed_state.peek(creation.record.yaml_path) == creation.seed


@pytest.mark.guard
@pytest.mark.parametrize("handoff", [False, True])
def test_creation_never_repairs_resume_or_handed_off_new(creation, capsys, handoff):
    if handoff:
        launch_seed_state.take(creation.record.yaml_path, seed_id=creation.seed.seed_id)
    else:
        launch_seed_state.stage(creation.record.yaml_path, kind="resume", text="another intent")
    before = launch_seed_state.state_path(creation.record.yaml_path).read_bytes()
    code, report = _run(creation, capsys, fix=True)
    assert code == 1 and "unclaimed retained New" in report["error"]
    assert launch_seed_state.state_path(creation.record.yaml_path).read_bytes() == before


@pytest.mark.parametrize("repeat", ["idempotent", "companion_permissions"])
def test_creation_restores_native_pair_permissions_and_is_idempotent(
    native_creation, capsys, repeat,
):
    creation = native_creation
    path = creation.record.yaml_path
    seed_before = launch_seed_state.state_path(path).read_bytes()
    code, report = _run(creation, capsys, fix=True)
    assert code == 0 and report["prepared"] and report["repaired"]
    assert all(report["checks"].values())
    assert report["projection_status"] == "ready"
    record = tracking.load_record(path)
    pair = state_root.resolve_pair(creation.configs["harness"], cwd=record.worktree_path)
    assert pair.paired and not pair.error and pair.sibling.role == "knowledge"
    knowledge_path = Path(pair.sibling.path)
    assert repair._permission_state(str(knowledge_path)) == {
        "trusted": True, "facility_approvals": True,
    }
    assert launch_seed_state.state_path(path).read_bytes() == seed_before
    before_records = sorted(creation.projects["knowledge"].joinpath("worktrees").glob("*.yaml"))
    if repeat == "companion_permissions":
        permission_path = permissions._permissions_path()
        saved = json.loads(permission_path.read_text())
        saved["locations"].pop(str(knowledge_path))
        permission_path.write_text(json.dumps(saved))
        code, report = _run(creation, capsys)
        assert code == 0 and report["checks"]["companion_permissions"] is False
    code, second = _run(creation, capsys, fix=True)
    assert code == 0 and second["prepared"]
    assert second["repaired"] == (repeat == "companion_permissions")
    assert second["checks"]["companion_permissions"]
    assert sorted(creation.projects["knowledge"].joinpath("worktrees").glob("*.yaml")) == before_records
    assert launch_seed_state.state_path(path).read_bytes() == seed_before


def test_creation_reuses_companion_after_parent_stamp_failure(native_creation, monkeypatch, capsys):
    creation = native_creation
    original = worktree_creation._stamp_and_compose_paired_knowledge
    monkeypatch.setattr(
        worktree_creation, "_stamp_and_compose_paired_knowledge",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("fixture publication failed")),
    )
    code, report = _run(creation, capsys, fix=True)
    assert code == 1 and "fixture publication failed" in report["error"]
    children = sorted(creation.projects["knowledge"].joinpath("worktrees").glob("*.yaml"))
    assert len(children) == 1 and not tracking.load_record(creation.record.yaml_path).is_paired
    monkeypatch.setattr(worktree_creation, "_stamp_and_compose_paired_knowledge", original)
    monkeypatch.setattr(
        worktree_creation, "_carve_paired_knowledge",
        lambda *a, **kw: pytest.fail("retained companion was allocated twice"),
    )
    code, report = _run(creation, capsys, fix=True)
    assert code == 0 and report["prepared"]
    assert sorted(creation.projects["knowledge"].joinpath("worktrees").glob("*.yaml")) == children
    assert launch_seed_state.peek(creation.record.yaml_path) == creation.seed


def test_creation_reports_projection_failure_without_claiming_seed(native_creation, monkeypatch, capsys):
    creation = native_creation
    monkeypatch.setattr(
        local_cache_refresh, "prepare_for_launch",
        lambda path: local_cache_refresh.RefreshResult("failed", detail="fixture renderer unavailable"),
    )
    code, report = _run(creation, capsys, fix=True)
    assert code == 1 and "fixture renderer unavailable" in report["error"]
    assert not report["repaired"] and "prepared" not in report
    assert launch_seed_state.peek(creation.record.yaml_path) == creation.seed


def test_creation_refuses_untracked_companion_before_permissions(creation, monkeypatch, capsys):
    target = creation.roots["knowledge"] / (
        f"host-{creation.short}-20261013-100000-abcd-k"
    )
    target.mkdir(parents=True)
    monkeypatch.setattr(
        permissions, "clone_permissions", lambda *a: pytest.fail("conflicted operation mutated"),
    )
    code, report = _run(creation, capsys, fix=True)
    assert code == 1 and "untracked paired checkout" in report["error"]
    assert launch_seed_state.peek(creation.record.yaml_path) == creation.seed


@pytest.mark.guard
def test_doctor_parser_routes_creation_only(creation, monkeypatch, capsys):
    parser = argparse.ArgumentParser()
    maintenance_cli.add_parsers(parser.add_subparsers(dest="command"))
    args = parser.parse_args([
        "doctor", "--repair-creation", creation.record.worktree_id,
        "--seed-id", creation.seed.seed_id, "--json",
    ])
    monkeypatch.setattr(
        maintenance_cli, "_core", lambda: pytest.fail("targeted repair entered a broad doctor pass"),
    )
    with repair.output.capture_json_output() as buffer:
        assert maintenance_cli.cmd_doctor(args) == 0
    assert json.loads(buffer.getvalue())["scope"] == "creation"


@pytest.mark.guard
def test_maintenance_dev_uses_shared_headless_spawn(tmp_path, monkeypatch):
    script = tmp_path / "tools" / "dev" / "setup.ps1"
    script.parent.mkdir(parents=True)
    script.touch()
    monkeypatch.setattr(maintenance_cli.worktree_identity, "_find_repo_dir", lambda: tmp_path)
    monkeypatch.setattr(maintenance_cli.sys, "platform", "win32")
    monkeypatch.setattr(maintenance_cli, "no_window_kwargs", lambda: {"creationflags": 123})

    def run(command, **kwargs):
        assert command == ["pwsh", "-NoProfile", "-File", str(script), "status"]
        assert kwargs == {"cwd": str(tmp_path), "creationflags": 123}
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(maintenance_cli.subprocess, "run", run)
    assert maintenance_cli.cmd_dev(SimpleNamespace(dev_action="status")) == 7


@pytest.mark.guard
def test_creation_rechecks_cold_state_after_lifecycle_lock(creation, monkeypatch, capsys):
    verdicts = iter([sessions.LiveVerdict(), sessions.LiveVerdict(active=True)])
    monkeypatch.setattr(sessions, "verify_worktree_active", lambda record: next(verdicts))
    monkeypatch.setattr(
        permissions, "clone_permissions", lambda *a: pytest.fail("lock-held live operation mutated"),
    )
    code, report = _run(creation, capsys, fix=True)
    assert code == 1 and "seed remains staged" in report["error"]
    assert launch_seed_state.peek(creation.record.yaml_path) == creation.seed


@pytest.mark.guard
@pytest.mark.parametrize("change", ["seed", "binding"])
def test_creation_refuses_changed_identity_during_setup(creation, monkeypatch, capsys, change):
    def changed(*args):
        if change == "seed":
            launch_seed_state.stage(creation.record.yaml_path, kind="new", text="changed intent")
        else:
            monkeypatch.setattr(
                state_root, "resolve_state_root",
                lambda *a, **kw: state_root.StateRoot("", "", "", False, False, True),
            )

    monkeypatch.setattr(repair, "_prepare_permissions", changed)
    monkeypatch.setattr(
        worktree_creation, "_carve_paired_knowledge",
        lambda *a, **kw: pytest.fail("changed identity allocated a companion"),
    )
    code, report = _run(creation, capsys, fix=True)
    assert code == 1 and report["error"] and not report["repaired"]


@pytest.mark.guard
@pytest.mark.parametrize("change", ["live", "binding", "identity"])
def test_creation_refuses_conflicting_retained_companion(creation, monkeypatch, capsys, change):
    child_id = creation.record.worktree_id + "-k"
    checkout = creation.roots["knowledge"] / child_id
    checkout.mkdir(parents=True)
    child = tracking.create_new_record(
        child_id, f"worktree/{child_id}", str(checkout), "knowledge", "host",
        cfg.detect_platform(), creation.projects["knowledge"] / "worktrees",
        pair_id="20261013-100000-abcd", pair_role="knowledge",
        pair_ref=tracking.format_claim_ref("host", "harness", creation.record.worktree_id),
        pair_kind="worktree",
    )
    if change == "live":
        monkeypatch.setattr(
            sessions, "verify_worktree_active",
            lambda record: sessions.LiveVerdict(active=record.repo == "knowledge"),
        )
    elif change == "binding":
        monkeypatch.setattr(
            state_root, "resolve_state_root",
            lambda *a, **kw: state_root.StateRoot(
                str(creation.anchors["harness"]), "", "harness", True, True, True,
            ),
        )
    else:
        child.branch = "worktree/unrelated"
        tracking.save_record(child)
    monkeypatch.setattr(
        permissions, "clone_permissions", lambda *a: pytest.fail("conflicted companion mutated"),
    )
    code, report = _run(creation, capsys, fix=True)
    assert code == 1 and report["error"]
    assert launch_seed_state.peek(creation.record.yaml_path) == creation.seed


@pytest.mark.guard
def test_creation_native_git_identity_check(tmp_path):
    for name in ("anchor", "checkout"):
        path = tmp_path / name
        path.mkdir()
        git_ops.git("init", cwd=path)
    with pytest.raises(ValueError, match="another Git repository"):
        repair._same_repository(str(tmp_path / "anchor"), str(tmp_path / "checkout"))
    with pytest.raises(ValueError, match="another Git branch"):
        repair._same_repository(
            str(tmp_path / "anchor"), str(tmp_path / "anchor"), branch="worktree/other",
        )


@pytest.mark.guard
@pytest.mark.parametrize("field", ["worktree_path", "branch", "platform"])
def test_creation_refuses_moved_or_mismatched_parent(creation, monkeypatch, capsys, field):
    value = str(creation.anchors["harness"]) if field == "worktree_path" else "other"
    setattr(creation.record, field, value)
    tracking.save_record(creation.record)
    monkeypatch.setattr(
        permissions, "clone_permissions", lambda *a: pytest.fail("mismatched parent mutated"),
    )
    code, report = _run(creation, capsys, fix=True)
    assert code == 1 and report["error"]
    assert launch_seed_state.peek(creation.record.yaml_path) == creation.seed


def test_creation_real_cli_first_touch_report_and_fix(tmp_path, monkeypatch):
    home, anchor = tmp_path / "home", tmp_path / "anchor"
    anchor.mkdir()
    git_ops.git("init", "-b", "main", cwd=anchor)
    registry = home / ".agent-worktrees"
    registry.mkdir(parents=True)
    state = home / ".fixture"
    records = state / "worktrees"
    records.mkdir(parents=True)
    root = tmp_path / "checkouts"
    platform = cfg.detect_platform()
    short = "win" if platform == "windows" else platform
    identity = f"host-{short}-20261013-100000-abcd"
    checkout = root / identity
    # A native linked worktree shares the Git directory without needing a commit.
    git_ops.git("worktree", "add", "--orphan", "-b", f"worktree/{identity}", str(checkout), cwd=anchor)
    config = {
        "machine": "host", "repo_name": "fixture",
        "repos": {"fixture": {
            "anchor": str(anchor), "worktree_root": str(root), "default_branch": "main",
        }},
    }
    (state / "config.yaml").write_text(json.dumps(config))
    (registry / "projects.yaml").write_text(json.dumps({
        "schema_version": 2, "projects": {"fixture": {"config_dir": str(state)}},
    }))
    monkeypatch.setenv("AGENT_HOME", str(home))
    monkeypatch.setattr(cfg, "_home", lambda: home)
    monkeypatch.setattr(status_monitor_runtime, "_status_monitor_enabled", lambda: False)
    record = tracking.create_new_record(
        identity, f"worktree/{identity}", str(checkout), "fixture", "host", platform, records,
    )
    seed = launch_seed_state.stage(record.yaml_path, kind="new", text="process fixture intent")
    before = launch_seed_state.state_path(record.yaml_path).read_bytes()
    copilot = home / ".copilot"
    copilot.mkdir()
    copilot_config = copilot / "config.json"
    copilot_config.write_text('{"trustedFolders":[]}')
    unrelated = records / "other.yaml"
    unrelated.write_bytes(b"unrelated: malformed: record\n")
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("COPILOT_", "AGENT_WORKTREES_"))
    }
    env.update({
        "AGENT_HOME": str(home), "HOME": str(home), "USERPROFILE": str(home),
        "AGENT_WORKTREES_STATUS_MONITOR": "0",
        "PYTHONPATH": str(Path(repair.__file__).resolve().parents[1]),
    })
    command = [
        sys.executable, "-m", "agent_worktrees", "--project", "fixture",
        "doctor", "--repair-creation", identity, "--seed-id", seed.seed_id, "--json",
    ]
    options = {
        "cwd": anchor, "env": env, "capture_output": True, "text": True, "timeout": 30,
        "creationflags": subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    }
    reported = subprocess.run(command, **options)
    assert reported.returncode == 0, reported.stdout + reported.stderr
    assert json.loads(reported.stdout)["repairable"]
    assert copilot_config.read_text() == '{"trustedFolders":[]}'
    assert not (copilot / "permissions-config.json").exists()
    applied = subprocess.run([*command, "--fix"], **options)
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert json.loads(applied.stdout)["prepared"]
    assert json.loads(applied.stdout)["projection_status"] == "ready"
    assert all(json.loads(applied.stdout)["checks"].values())
    assert launch_seed_state.state_path(record.yaml_path).read_bytes() == before
    assert unrelated.read_bytes() == b"unrelated: malformed: record\n"
    assert seed.text not in reported.stdout + reported.stderr + applied.stdout + applied.stderr
