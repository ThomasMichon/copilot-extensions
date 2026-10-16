"""Resolve previews preserve durable state, including their failure paths."""

from __future__ import annotations

import dataclasses
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from agent_worktrees import __main__ as main
from agent_worktrees import activity, config as cfg, launch_seed_state
from agent_worktrees import profile_assignment, resolve_cli, sessions, tracking
from agent_worktrees.launch_progress import preview_mode, step

pytestmark = pytest.mark.guard


@dataclasses.dataclass
class PreviewCase:
    root: Path
    config: cfg.Config
    record: tracking.WorktreeRecord


def _snapshot(root: Path) -> dict[str, tuple[bytes, int] | None]:
    return {
        str(path.relative_to(root)): (
            (path.read_bytes(), path.stat().st_mtime_ns) if path.is_file() else None
        )
        for path in root.rglob("*")
    }


@pytest.fixture
def case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, monkeypatch_config: None) -> PreviewCase:
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    worktree = tmp_path / "checkout"
    worktree.mkdir()
    config = cfg.Config(
        srcroot=str(tmp_path), machine="test", platform=cfg.detect_platform(),
        repo_name="demo", auto_fast_forward=False,
        repos={"demo": cfg.RepoConfig(
            anchor=str(anchor), worktree_root=str(tmp_path / "children"),
            launch={"windows": ["copilot"], "linux": ["copilot"]},
            session_env={"PREVIEW_CHECK": "{work_dir}"},
        )},
    )
    cfg.set_active_project("demo")
    session_id = "00000000-0000-0000-0000-000000000001"
    session_root = tmp_path / "session-state"
    session_dir = session_root / session_id
    session_dir.mkdir(parents=True)
    (session_dir / "events.jsonl").write_text("{}\n", encoding="utf-8")
    (session_dir / "workspace.yaml").write_text(
        "updated_at: '2026-10-01T00:00:00'\n", encoding="utf-8",
    )
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: session_root)
    path = cfg.tracking_dir() / "wt-preview.yaml"
    path.write_text(yaml.safe_dump({
        "worktree_id": "wt-preview", "branch": "worktree/preview",
        "worktree_path": str(worktree), "repo": "demo", "machine": "test",
        "platform": config.platform, "started_at": "2026-10-01T00:00:00",
        "last_resumed_at": "2026-10-01T00:00:00", "resume_count": 7,
        "status": "active", "sessions": [{"session_id": session_id, "started_at": "2026-10-01T00:00:00"}],
    }), encoding="utf-8")
    record = tracking.load_record(path)
    result = PreviewCase(tmp_path, config, record)
    monkeypatch.setattr(cfg, "load_config", lambda *args, **kwargs: result.config)
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda *args, **kwargs: {})
    monkeypatch.setattr(sessions, "verify_worktree_active", lambda *args: sessions.LiveVerdict())
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
    log = tmp_path / "logs" / "activity.jsonl"
    log.parent.mkdir()
    log.write_text('{"event":"sentinel"}\n', encoding="utf-8")
    monkeypatch.setattr(activity, "log_path", lambda: log)
    monkeypatch.setattr(activity, "_PRUNE_SIZE_BYTES", 1)

    def forbid_prune(*args: object) -> None:
        pytest.fail("preview dispatched an activity prune worker")

    monkeypatch.setattr(activity, "_dispatch_background_prune", forbid_prune)
    trace = tmp_path / "trace.jsonl"
    trace.write_text('{"event":"sentinel"}\n', encoding="utf-8")
    monkeypatch.setenv("AGENT_WORKTREES_LAUNCH_TRACE", str(trace))
    return result


def _resolve(case: PreviewCase, *, json_mode: bool = True, **options: bool | str) -> int:
    argv = ["resolve", "--worktree-id", case.record.worktree_id, "--dry-run", "--no-fast-forward"]
    if json_mode:
        argv.append("--json")
    for key, value in options.items():
        argv.append("--" + key.replace("_", "-"))
        if not isinstance(value, bool):
            argv.append(value)
    return resolve_cli.cmd_resolve(main.build_parser().parse_args(argv))


@pytest.mark.parametrize("json_mode", [True, False])
def test_preview_twice_preserves_files_and_ordinary_resume_still_records(
    case: PreviewCase, capfd: pytest.CaptureFixture[str], json_mode: bool,
) -> None:
    before = _snapshot(case.root)
    for _ in range(2):
        assert _resolve(case, json_mode=json_mode) == 0
        payload = json.loads(capfd.readouterr().out)
        if json_mode:
            assert payload["worktree"]["resume_count"] == 7
            assert payload["launch"]["preview"] is True
            assert payload["launch"]["work_dir"] == case.record.worktree_path
            assert payload["launch"]["env"]["PREVIEW_CHECK"] == case.record.worktree_path
            assert "--admission-only" in payload["launch"]["cmd"]
            assert "--experimental" in payload["launch"]["cmd"]
            assert "--resume=00000000-0000-0000-0000-000000000001" in payload["launch"]["cmd"]
        else:
            assert payload["action"] == "none" and payload["exit_code"] == 0
        assert _snapshot(case.root) == before

    args = main.build_parser().parse_args([
        "resolve", "--json", "--worktree-id", case.record.worktree_id, "--defer-projections",
    ])
    # The positive control exercises real activity but does not dispatch its unrelated prune worker.
    activity._PRUNE_SIZE_BYTES = 1024 * 1024
    assert resolve_cli.cmd_resolve(args) == 0
    capfd.readouterr()
    assert tracking.load_record(case.record.yaml_path).resume_count == 8
    assert case.record.yaml_path.with_suffix(".lock").exists()
    events = (case.root / "logs" / "activity.jsonl").read_text(encoding="utf-8")
    assert "worktree_resumed" in events and "launch_preparation" in events


def test_terminal_live_and_seed_previews_preserve_snapshots(
    case: PreviewCase, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    data = yaml.safe_load(case.record.yaml_path.read_text(encoding="utf-8"))
    data["status"] = "finalized"
    case.record.yaml_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    seed_path = launch_seed_state.state_path(case.record.yaml_path)
    seed_path.parent.mkdir()
    seed_path.write_text(json.dumps({
        "version": 1, "seed_id": "saved-seed", "kind": "resume",
        "text": "preserve this prompt", "revision": 0,
    }), encoding="utf-8")
    before = _snapshot(case.root)
    for _ in range(2):
        assert _resolve(case, seed_id="saved-seed") == 0
        plan = json.loads(capfd.readouterr().out)["launch"]
        assert plan["seed_id"] == "saved-seed" and plan["seed_pending"]
        assert _resolve(case, seed="preview replacement") == 0
        plan = json.loads(capfd.readouterr().out)["launch"]
        assert plan["seed_id"] == "preview" and plan["seed_kind"] == "resume"
        assert _snapshot(case.root) == before
    monkeypatch.setattr(
        sessions, "verify_worktree_active",
        lambda *args: sessions.LiveVerdict(active=True, mux_live=True),
    )
    for _ in range(2):
        assert _resolve(case) == 0
        assert not json.loads(capfd.readouterr().out)["launch"]["seed_pending"]
        assert _resolve(case, seed="cannot deliver live") == 3
        assert "cold-start-only" in json.loads(capfd.readouterr().out)["error"]
        assert _snapshot(case.root) == before


@pytest.mark.parametrize("json_mode", [True, False])
def test_preview_refusals_do_not_write(
    case: PreviewCase, monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str], json_mode: bool,
) -> None:
    def forbid_restore(*args: object, **kwargs: object) -> None:
        pytest.fail("preview attempted to restore or reclaim a process")

    monkeypatch.setattr(main, "_perform_remux", forbid_restore)
    monkeypatch.setattr(main, "_restore_before_resume", forbid_restore)
    before = _snapshot(case.root)
    for _ in range(2):
        platform_refusal = json_mode and platform.system() != "Windows"
        assert _resolve(case, json_mode=json_mode, restore=True) == (1 if platform_refusal else 3)
        error = json.loads(capfd.readouterr().out)["error"]
        if platform_refusal:
            assert "--restore with --json is unsupported on Linux/WSL" in error
        else:
            assert "restore preview is unsupported" in error
        assert _snapshot(case.root) == before

    repo = case.config.default_repo
    case.config = dataclasses.replace(case.config, repos={"demo": dataclasses.replace(
        repo, launch={}, setup_hook={"windows": "setup.ps1", "linux": "setup.sh"}, stateless=True,
    )})
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: Path(repo.anchor) / "unsafe-config")
    before = _snapshot(case.root)
    for _ in range(2):
        assert _resolve(case, json_mode=json_mode) == 3
        assert "stateless checkout" in json.loads(capfd.readouterr().out)["error"]
        assert _snapshot(case.root) == before


@pytest.mark.parametrize("json_mode", [True, False])
def test_wsl_preview_runs_linux_setup_preflight_without_writes(
    case: PreviewCase, monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str], json_mode: bool,
) -> None:
    repo = case.config.default_repo
    case.config = dataclasses.replace(case.config, platform="wsl", repos={"demo": dataclasses.replace(
        repo, launch={}, setup_hook={"windows": "setup.ps1", "linux": "setup.sh"}, stateless=True,
    )})
    monkeypatch.setattr(cfg, "detect_platform", lambda: "wsl")
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: Path(repo.anchor) / "unsafe-config")
    before = _snapshot(case.root)
    for _ in range(2):
        assert _resolve(case, json_mode=json_mode) == 3
        assert "stateless checkout" in json.loads(capfd.readouterr().out)["error"]
        assert _snapshot(case.root) == before


@pytest.mark.parametrize("json_mode", [True, False])
def test_foreign_space_preview_still_refuses(
    case: PreviewCase, monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str], json_mode: bool,
) -> None:
    platform = case.config.platform
    foreign = "wsl" if platform == "windows" else "windows"
    entries = {
        name: cfg.MachineEntry(
            key=name, display_name=name, execution_platform=execution_platform,
            hostname="same-physical-host", physical_host="same-physical-host",
        )
        for name, execution_platform in (("test", platform), ("foreign", foreign))
    }
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda *args, **kwargs: entries)
    data = yaml.safe_load(case.record.yaml_path.read_text(encoding="utf-8"))
    data.update(machine="foreign", platform=foreign)
    case.record.yaml_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    before = _snapshot(case.root)
    for _ in range(2):
        assert _resolve(case, json_mode=json_mode) == 3
        assert "different execution space" in json.loads(capfd.readouterr().out)["error"]
        assert _snapshot(case.root) == before


@pytest.mark.parametrize("json_mode", [True, False])
def test_random_assignment_is_deferred_not_reserved(
    case: PreviewCase, capfd: pytest.CaptureFixture[str], json_mode: bool,
) -> None:
    case.config = dataclasses.replace(
        case.config,
        copilot_profiles=[cfg.CopilotProfile(name="choice", label="Choice", copilot_args=["--model", "choice"])],
        profile_assignment=cfg.ProfileAssignmentPolicy(
            name="balanced", mode="balanced-random", armed=True, profiles=("choice",),
        ),
    )
    before = _snapshot(case.root)
    for _ in range(2):
        assert _resolve(case, json_mode=json_mode, no_resume=True) == 0
        captured = capfd.readouterr()
        if json_mode:
            plan = json.loads(captured.out)["launch"]
            assert "reserves no profile" in plan["preview_warning"]
            assert "profile_assignment" not in plan
            assert profile_assignment.ASSIGNMENT_TOKEN_ENV not in plan["env"]
        else:
            assert "reserves no profile" in captured.err
        assert _snapshot(case.root) == before
        assert not profile_assignment.state_path().exists()


def test_manual_and_replayed_profile_previews_retain_launch_details(
    case: PreviewCase, capfd: pytest.CaptureFixture[str],
) -> None:
    case.config = dataclasses.replace(case.config, copilot_profiles=[cfg.CopilotProfile(
        name="choice", label="Choice", copilot_args=["--model", "choice"], env={"PROFILE_ENV": "choice"},
    )])
    for manual in (True, False):
        if not manual:
            data = yaml.safe_load(case.record.yaml_path.read_text(encoding="utf-8"))
            data["profile_assignments"] = [dataclasses.asdict(tracking.ProfileAssignment(
                policy="balanced", assignment_label="group", selected_profile="choice",
                bag_generation=0, bag_position=0, assigned_at="2026-10-01T00:00:00",
                disposition="bound", session_id="00000000-0000-0000-0000-000000000001",
            ))]
            case.record.yaml_path.write_text(yaml.safe_dump(data), encoding="utf-8")
        before = _snapshot(case.root)
        for _ in range(2):
            assert _resolve(case, **({"profile": "choice"} if manual else {})) == 0
            plan = json.loads(capfd.readouterr().out)["launch"]
            assert plan["cmd"][plan["cmd"].index("--model") + 1] == "choice"
            assert plan["env"]["PROFILE_ENV"] == "choice"
            assert "preview_warning" not in plan
            if not manual:
                assert plan["profile_assignment"]["selected_profile"] == "choice"
            assert _snapshot(case.root) == before


def test_seed_snapshot_preserves_revision_fence_without_a_writer_lock(
    case: PreviewCase, capfd: pytest.CaptureFixture[str],
) -> None:
    seed_path = launch_seed_state.state_path(case.record.yaml_path)
    seed_path.parent.mkdir()
    seed_path.write_text(json.dumps({
        "version": 1, "seed_id": "superseded", "kind": "resume", "text": "old", "revision": 0,
    }), encoding="utf-8")
    data = yaml.safe_load(case.record.yaml_path.read_text(encoding="utf-8"))
    data["pending_seed_revision"] = 1
    case.record.yaml_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    before = _snapshot(case.root)
    for _ in range(2):
        assert launch_seed_state.peek(case.record.yaml_path, read_only=True) is None
        assert _resolve(case, seed_id="superseded") == 3
        assert "missing or superseded" in json.loads(capfd.readouterr().out)["error"]
        assert _snapshot(case.root) == before
    data["pending_seed"] = "newer legacy intent"
    case.record.yaml_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    before = _snapshot(case.root)
    for _ in range(2):
        assert launch_seed_state.peek(case.record.yaml_path, read_only=True) is None
        assert _resolve(case, seed_id="superseded") == 3
        assert "missing or superseded" in json.loads(capfd.readouterr().out)["error"]
        assert _resolve(case) == 0
        plan = json.loads(capfd.readouterr().out)["launch"]
        assert plan["seed_kind"] == "new" and plan["seed_id"] == "preview"
        assert _snapshot(case.root) == before
    assert launch_seed_state.peek(case.record.yaml_path).seed_id == "superseded"
    assert case.record.yaml_path.with_suffix(".lock").exists()


@pytest.mark.parametrize("json_mode", [True, False])
def test_typed_seed_preview_survives_interrupted_legacy_migration(
    case: PreviewCase, capfd: pytest.CaptureFixture[str], json_mode: bool,
) -> None:
    seed_path = launch_seed_state.state_path(case.record.yaml_path)
    seed_path.parent.mkdir()
    seed_path.write_text(json.dumps({
        "version": 1, "seed_id": "typed-current", "kind": "resume",
        "text": "current typed intent", "revision": 2,
    }), encoding="utf-8")
    data = yaml.safe_load(case.record.yaml_path.read_text(encoding="utf-8"))
    data["pending_seed"] = "older legacy intent"
    for revision in (0, 2):
        data["pending_seed_revision"] = revision
        case.record.yaml_path.write_text(yaml.safe_dump(data), encoding="utf-8")
        before = _snapshot(case.root)
        for _ in range(2):
            assert _resolve(case, json_mode=json_mode, seed_id="typed-current") == 0
            payload = json.loads(capfd.readouterr().out)
            if json_mode:
                assert payload["launch"]["seed_id"] == "typed-current"
                assert payload["launch"]["seed_kind"] == "resume"
            else:
                assert payload["action"] == "none"
            assert _snapshot(case.root) == before
    result = launch_seed_state._apply_stage({
        "yaml_path": str(case.record.yaml_path), "worktree_id": case.record.worktree_id,
    })
    assert result["seed"]["seed_id"] == "typed-current"
    assert tracking.load_record(case.record.yaml_path).pending_seed is None


def test_new_preview_and_unselected_picker_refuse_without_writing(
    case: PreviewCase, capfd: pytest.CaptureFixture[str],
) -> None:
    before = _snapshot(case.root)
    for _ in range(2):
        for options, code in ((["--json", "--new"], 1), ([], 2)):
            args = main.build_parser().parse_args(["resolve", "--dry-run", *options])
            assert resolve_cli.cmd_resolve(args) == code
            captured = capfd.readouterr()
            assert "unsupported" in captured.out or "no picker was opened" in captured.err
            assert _snapshot(case.root) == before


def test_base_json_preview_keeps_flat_launch_shape_without_writes(
    case: PreviewCase, capfd: pytest.CaptureFixture[str],
) -> None:
    before = _snapshot(case.root)
    for _ in range(2):
        args = main.build_parser().parse_args(["resolve", "--json", "--base", "--dry-run"])
        assert resolve_cli.cmd_resolve(args) == 0
        plan = json.loads(capfd.readouterr().out)
        assert plan["preview"] is True
        assert plan["work_dir"] == case.config.default_repo.anchor
        assert plan["post_exit"] is False and "launch" not in plan
        assert _snapshot(case.root) == before


def test_explicit_base_interactive_preview_does_not_open_the_picker(
    case: PreviewCase, capfd: pytest.CaptureFixture[str],
) -> None:
    assert not case.config.default_repo.base_repo
    before = _snapshot(case.root)
    for _ in range(2):
        args = main.build_parser().parse_args(["resolve", "--base", "--dry-run"])
        assert resolve_cli.cmd_resolve(args) == 0
        captured = capfd.readouterr()
        assert json.loads(captured.out)["action"] == "none"
        assert case.config.default_repo.anchor in captured.err
        assert _snapshot(case.root) == before


@pytest.mark.parametrize("json_mode", [True, False])
def test_base_preview_preserves_stale_git_index(
    case: PreviewCase, monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str], json_mode: bool,
) -> None:
    anchor = Path(case.config.default_repo.anchor)
    git = main.git_ops.git
    git("init", "--quiet", cwd=anchor, isolated_repository=True, timeout=10)
    tracked = anchor / "tracked.txt"
    tracked.write_text("unchanged content\n", encoding="utf-8")
    git("add", "tracked.txt", cwd=anchor, isolated_repository=True, timeout=10)
    git(
        "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Initial fixture",
        cwd=anchor, isolated_repository=True, no_hooks=True, timeout=10,
    )
    original = tracked.stat()
    os.utime(tracked, ns=(original.st_atime_ns, original.st_mtime_ns + 2_000_000_000))
    monkeypatch.setenv("GIT_OPTIONAL_LOCKS", "1")
    index = anchor / ".git" / "index"
    index_bytes, index_mtime = index.read_bytes(), index.stat().st_mtime_ns
    before = _snapshot(case.root)
    for _ in range(2):
        args = main.build_parser().parse_args([
            "resolve", "--base", "--dry-run", *(["--json"] if json_mode else []),
        ])
        assert resolve_cli.cmd_resolve(args) == 0
        capfd.readouterr()
        assert index.read_bytes() == index_bytes
        assert index.stat().st_mtime_ns == index_mtime
        assert _snapshot(case.root) == before

    assert main.git_ops.get_dirty_files(anchor) == []
    assert index.read_bytes() != index_bytes
    assert index.stat().st_mtime_ns != index_mtime


def test_preview_progress_heartbeat_and_failure_leave_no_activity(
    case: PreviewCase, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading
    from agent_worktrees import launch_progress

    monkeypatch.setattr(launch_progress, "HEARTBEAT_SECONDS", 0.01)
    before = _snapshot(case.root)
    for _ in range(2):
        with pytest.raises(ValueError, match="invalid preview"):
            with preview_mode(True), step("Preview phase"):
                threading.Event().wait(0.03)
                raise ValueError("invalid preview")
        assert _snapshot(case.root) == before


def test_native_cli_preview_twice_is_read_only(
    case: PreviewCase,
) -> None:
    """One real process crosses the CLI dispatch boundary twice, without launching Copilot."""
    driver = """
import sys
from pathlib import Path
from agent_worktrees import __main__ as main, activity, config as cfg, sessions
main._load_full_command_surface()
root = Path(sys.argv[1])
cfg.set_active_project("demo")
cfg.tracking_dir = lambda: root / "worktrees"
cfg.project_dir = lambda name=None: root / ".demo"
config = cfg.Config(srcroot=str(root), machine="test", platform=cfg.detect_platform(), repo_name="demo",
    repos={"demo": cfg.RepoConfig(anchor=str(root / "anchor"),
        worktree_root=str(root / "children"), launch={"windows": ["copilot"], "linux": ["copilot"]})})
cfg.load_config = lambda *args, **kwargs: config
cfg.load_machines_yaml = lambda *args, **kwargs: {}
activity.log_path = lambda: root / "logs" / "activity.jsonl"
activity._PRUNE_SIZE_BYTES = 1
def forbid_prune(*args):
    raise AssertionError("preview dispatched an activity prune worker")
activity._dispatch_background_prune = forbid_prune
sessions.verify_worktree_active = lambda *args: sessions.LiveVerdict()
sessions._session_state_dir = lambda: root / "session-state"
main.front_door_cli.resolve_invocation_project = lambda *args: True
for _ in range(2):
    assert main.main(["resolve", "--json", "--dry-run", "--no-fast-forward",
        "--worktree-id", "wt-preview"]) == 0
"""
    before = _snapshot(case.root)
    result = subprocess.run(
        [sys.executable, "-c", driver, str(case.root)],
        capture_output=True, text=True, timeout=20, env=dict(os.environ),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    decoder = json.JSONDecoder()
    remaining = result.stdout.strip()
    plans = []
    while remaining:
        plan, end = decoder.raw_decode(remaining)
        plans.append(plan)
        remaining = remaining[end:].strip()
    assert len(plans) == 2
    assert all(plan["launch"]["preview"] for plan in plans)
    assert _snapshot(case.root) == before
