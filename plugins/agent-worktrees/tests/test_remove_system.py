"""Tests for explicit managed-worktree removal."""

from __future__ import annotations

import argparse
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_worktrees import __main__ as cli
from agent_worktrees import git_ops
from agent_worktrees import tracking


def _record(tmp_path: Path) -> tuple[tracking.WorktreeRecord, Path]:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    record = tracking.WorktreeRecord(
        worktree_id="managed-1",
        branch="worktree/managed-1",
        worktree_path=str(worktree),
        repo="demo",
        machine="machine",
        platform="windows",
        started_at="2026-09-01T00:00:00",
        last_resumed_at="2026-09-01T00:00:00",
        resume_count=0,
        title=None,
        status="finalized",
        completed_at=None,
        sessions=[],
        prs=[],
        kind="bridge",
    )
    tracking_dir = tmp_path / "tracking"
    tracking_dir.mkdir()
    tracking.save_record(record, tracking_dir / "managed-1.yaml")
    return record, tracking_dir


def _config(tmp_path: Path):
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    return types.SimpleNamespace(
        default_repo=types.SimpleNamespace(
            anchor=str(anchor), remote="origin", default_branch="master",
            worktree_root=str(tmp_path / "worktree_root"),
        )
    )


def test_remove_system_retains_record_when_worktree_removal_fails(
    tmp_path,
):
    record, tracking_dir = _record(tmp_path)
    args = argparse.Namespace(worktree_id=record.worktree_id, json=True)

    with patch("agent_worktrees.config.load_config", return_value=_config(tmp_path)), \
         patch("agent_worktrees.config.tracking_dir", return_value=tracking_dir), \
         patch("agent_worktrees.sessions.kill_tmux_session"), \
         patch(
             "agent_worktrees.git_ops.list_worktree_paths",
             return_value=[record.worktree_path],
         ), \
         patch("agent_worktrees.git_ops.remove_worktree", return_value=False), \
         patch("agent_worktrees.__main__._json_output") as json_output:
        result = cli.cmd_remove_system(args)

    assert result == 1
    payload = json_output.call_args.args[0]
    assert "worktree remove failed" in payload["error"]
    assert "tracking record retained for retry" in payload["error"]
    assert (tracking_dir / "managed-1.yaml").exists()
    assert Path(record.worktree_path).exists()


def test_remove_system_deletes_record_after_worktree_removal_succeeds(
    tmp_path,
):
    record, tracking_dir = _record(tmp_path)
    args = argparse.Namespace(worktree_id=record.worktree_id, json=True)

    with patch("agent_worktrees.config.load_config", return_value=_config(tmp_path)), \
         patch("agent_worktrees.config.tracking_dir", return_value=tracking_dir), \
         patch("agent_worktrees.sessions.kill_tmux_session"), \
         patch("agent_worktrees.git_ops.list_worktree_paths"), \
         patch("agent_worktrees.git_ops.remove_worktree", return_value=True), \
         patch("agent_worktrees.git_ops.git") as git, \
         patch("agent_worktrees.disposition_history.remove"), \
         patch("agent_worktrees.activity.log_event"), \
         patch("agent_worktrees.__main__._json_output") as json_output:
        git.return_value.returncode = 0
        git.return_value.stdout = "0"
        result = cli.cmd_remove_system(args)

    assert result == 0
    json_output.assert_called_once_with({"removed": "managed-1"})
    assert not (tracking_dir / "managed-1.yaml").exists()


def test_remove_system_deletes_unregistered_leftover_and_record(
    tmp_path,
):
    record, tracking_dir = _record(tmp_path)
    args = argparse.Namespace(worktree_id=record.worktree_id, json=True)

    with patch("agent_worktrees.config.load_config", return_value=_config(tmp_path)), \
         patch("agent_worktrees.config.tracking_dir", return_value=tracking_dir), \
         patch("agent_worktrees.sessions.kill_tmux_session"), \
         patch("agent_worktrees.git_ops.list_worktree_paths", return_value=[]), \
         patch(
             "agent_worktrees.git_ops.remove_worktree",
             return_value=False,
         ) as remove_worktree, \
         patch("agent_worktrees.git_ops.git") as git, \
         patch("agent_worktrees.disposition_history.remove"), \
         patch("agent_worktrees.activity.log_event"), \
         patch("agent_worktrees.__main__._json_output"):
        git.return_value.returncode = 0
        git.return_value.stdout = "0"
        result = cli.cmd_remove_system(args)

    assert result == 0
    remove_worktree.assert_called_once_with(
        str(tmp_path / "anchor"),
        record.worktree_path,
    )
    assert not Path(record.worktree_path).exists()
    assert not (tracking_dir / "managed-1.yaml").exists()


def test_remove_system_preserves_unregistered_nonempty_path(
    tmp_path,
):
    record, tracking_dir = _record(tmp_path)
    data = Path(record.worktree_path) / "keep.txt"
    data.write_text("unrelated data", encoding="utf-8")
    args = argparse.Namespace(worktree_id=record.worktree_id, json=True)

    with patch("agent_worktrees.config.load_config", return_value=_config(tmp_path)), \
         patch("agent_worktrees.config.tracking_dir", return_value=tracking_dir), \
         patch("agent_worktrees.sessions.kill_tmux_session"), \
         patch("agent_worktrees.git_ops.remove_worktree", return_value=False), \
         patch("agent_worktrees.git_ops.list_worktree_paths", return_value=[]), \
         patch("agent_worktrees.__main__._json_output"):
        result = cli.cmd_remove_system(args)

    assert result == 1
    assert data.read_text(encoding="utf-8") == "unrelated data"
    assert (tracking_dir / "managed-1.yaml").exists()


def test_managed_gc_uses_non_force_removal_for_registered_worktree(
    tmp_path,
):
    record, tracking_dir = _record(tmp_path)
    record.branch = ""
    repo = _config(tmp_path).default_repo

    with patch("agent_worktrees.sessions.has_mux_session", return_value=False), \
         patch(
             "agent_worktrees.sessions.scan_sessions_fast",
             return_value=types.SimpleNamespace(active_sessions={}),
         ), \
         patch(
             "agent_worktrees.git_ops.list_worktree_paths",
             return_value=[record.worktree_path],
         ), \
         patch("agent_worktrees.git_ops.git") as git, \
         patch("agent_worktrees.disposition_history.remove"):
        git.return_value.returncode = 0
        removed, warnings = cli._remove_managed_worktree(
            record,
            repo,
            tracking_dir,
        )

    assert removed is True
    assert warnings == []
    worktree_remove = git.call_args_list[0]
    assert worktree_remove.args[:3] == (
        "worktree",
        "remove",
        record.worktree_path,
    )
    assert "--force" not in worktree_remove.args


def test_remove_system_retains_record_when_registration_probe_fails(
    tmp_path,
):
    record, tracking_dir = _record(tmp_path)
    args = argparse.Namespace(worktree_id=record.worktree_id, json=True)

    with patch("agent_worktrees.config.load_config", return_value=_config(tmp_path)), \
         patch("agent_worktrees.config.tracking_dir", return_value=tracking_dir), \
         patch("agent_worktrees.sessions.kill_tmux_session"), \
         patch("agent_worktrees.git_ops.remove_worktree", return_value=False), \
         patch(
             "agent_worktrees.git_ops.list_worktree_paths",
             side_effect=RuntimeError("registration probe failed"),
         ), \
         patch("agent_worktrees.__main__._json_output"):
        result = cli.cmd_remove_system(args)

    assert result == 1
    assert Path(record.worktree_path).exists()
    assert (tracking_dir / "managed-1.yaml").exists()


def test_remove_system_retains_missing_path_when_still_registered(
    tmp_path,
):
    record, tracking_dir = _record(tmp_path)
    Path(record.worktree_path).rmdir()
    args = argparse.Namespace(worktree_id=record.worktree_id, json=True)

    with patch("agent_worktrees.config.load_config", return_value=_config(tmp_path)), \
         patch("agent_worktrees.config.tracking_dir", return_value=tracking_dir), \
         patch("agent_worktrees.sessions.kill_tmux_session"), \
         patch("agent_worktrees.git_ops.remove_worktree", return_value=False), \
         patch(
             "agent_worktrees.git_ops.list_worktree_paths",
             return_value=[record.worktree_path],
         ), \
         patch("agent_worktrees.__main__._json_output"):
        result = cli.cmd_remove_system(args)

    assert result == 1
    assert (tracking_dir / "managed-1.yaml").exists()


def test_remove_system_retains_record_when_yaml_unlink_fails(
    tmp_path,
):
    record, tracking_dir = _record(tmp_path)
    record.worktree_path = ""
    record.branch = ""

    with patch("pathlib.Path.unlink", side_effect=PermissionError("locked")):
        removed, warnings = cli._remove_managed_worktree(
            record,
            _config(tmp_path).default_repo,
            tracking_dir,
            force=True,
        )

    assert removed is False
    assert warnings == ["tracking record remove failed: locked"]
    assert (tracking_dir / "managed-1.yaml").exists()


def test_worktree_registration_probe_can_fail_closed(tmp_path):
    failure = types.SimpleNamespace(
        returncode=128,
        stdout="",
        stderr="fatal: not a git repository",
    )

    with patch("agent_worktrees.git_ops.git", return_value=failure), \
         pytest.raises(RuntimeError, match="not a git repository"):
        git_ops.list_worktree_paths(cwd=tmp_path, fail_on_error=True)


def _init_dirty_git_worktree(worktree: Path) -> None:
    """Turn a plain directory into a minimal real git repo with one
    uncommitted change, so ``git_ops.classify_worktree`` reports DIRTY."""
    import subprocess

    def _git(*args):
        subprocess.run(
            ["git", *args], cwd=worktree, check=True,
            capture_output=True, text=True,
        )

    _git("init", "--quiet")
    _git("config", "user.email", "test@example.com")
    _git("config", "user.name", "Test")
    (worktree / "committed.txt").write_text("v1", encoding="utf-8")
    _git("add", "committed.txt")
    _git("commit", "--quiet", "-m", "initial")
    (worktree / "uncommitted.txt").write_text("scratch", encoding="utf-8")


def test_remove_system_refuses_dirty_worktree_by_default(tmp_path):
    record, tracking_dir = _record(tmp_path)
    _init_dirty_git_worktree(Path(record.worktree_path))
    args = argparse.Namespace(worktree_id=record.worktree_id, json=True, force=False)

    with patch("agent_worktrees.config.load_config", return_value=_config(tmp_path)), \
         patch("agent_worktrees.config.tracking_dir", return_value=tracking_dir), \
         patch("agent_worktrees.__main__._json_error") as json_error:
        json_error.return_value = 1
        result = cli.cmd_remove_system(args)

    assert result == 1
    message = json_error.call_args.args[0]
    assert "uncommitted change" in message
    # Nothing was torn down: the record and the working tree survive.
    assert (tracking_dir / "managed-1.yaml").exists()
    assert (Path(record.worktree_path) / "uncommitted.txt").exists()


def test_remove_system_refuses_open_pr_by_default(tmp_path):
    record, tracking_dir = _record(tmp_path)
    record.prs = [tracking.PRRecord(state="open", url="https://example/pulls/9", number=9)]
    tracking.save_record(record, tracking_dir / "managed-1.yaml")
    args = argparse.Namespace(worktree_id=record.worktree_id, json=True, force=False)

    with patch("agent_worktrees.config.load_config", return_value=_config(tmp_path)), \
         patch("agent_worktrees.config.tracking_dir", return_value=tracking_dir), \
         patch("agent_worktrees.__main__._json_error") as json_error:
        json_error.return_value = 1
        result = cli.cmd_remove_system(args)

    assert result == 1
    message = json_error.call_args.args[0]
    assert "open PR" in message
    assert "https://example/pulls/9" in message
    assert (tracking_dir / "managed-1.yaml").exists()


def test_remove_system_refuses_live_resource_claim_by_default(tmp_path):
    record, tracking_dir = _record(tmp_path)
    record.resources = [
        tracking.ResourceClaim(kind="codespace", ref="example-cs-1", state="active")
    ]
    tracking.save_record(record, tracking_dir / "managed-1.yaml")
    args = argparse.Namespace(worktree_id=record.worktree_id, json=True, force=False)

    with patch("agent_worktrees.config.load_config", return_value=_config(tmp_path)), \
         patch("agent_worktrees.config.tracking_dir", return_value=tracking_dir), \
         patch("agent_worktrees.__main__._json_error") as json_error:
        json_error.return_value = 1
        result = cli.cmd_remove_system(args)

    assert result == 1
    message = json_error.call_args.args[0]
    assert "unsettled outbound resource claim" in message
    assert "codespace:example-cs-1" in message
    assert (tracking_dir / "managed-1.yaml").exists()


def test_remove_system_bridge_kind_blocker_mentions_owning_service(tmp_path):
    record, tracking_dir = _record(tmp_path)
    record.resources = [
        tracking.ResourceClaim(kind="codespace", ref="example-cs-1", state="active")
    ]
    tracking.save_record(record, tracking_dir / "managed-1.yaml")
    args = argparse.Namespace(worktree_id=record.worktree_id, json=True, force=False)

    with patch("agent_worktrees.config.load_config", return_value=_config(tmp_path)), \
         patch("agent_worktrees.config.tracking_dir", return_value=tracking_dir), \
         patch("agent_worktrees.__main__._json_error") as json_error:
        json_error.return_value = 1
        cli.cmd_remove_system(args)

    message = json_error.call_args.args[0]
    assert "agent-bridge" in message


def test_remove_system_force_bypasses_all_guards(tmp_path):
    record, tracking_dir = _record(tmp_path)
    _init_dirty_git_worktree(Path(record.worktree_path))
    record.prs = [tracking.PRRecord(state="open", url="https://example/pulls/9", number=9)]
    record.resources = [
        tracking.ResourceClaim(kind="codespace", ref="example-cs-1", state="active")
    ]
    tracking.save_record(record, tracking_dir / "managed-1.yaml")
    args = argparse.Namespace(worktree_id=record.worktree_id, json=True, force=True)

    with patch("agent_worktrees.config.load_config", return_value=_config(tmp_path)), \
         patch("agent_worktrees.config.tracking_dir", return_value=tracking_dir), \
         patch("agent_worktrees.sessions.kill_tmux_session"), \
         patch("agent_worktrees.git_ops.list_worktree_paths"), \
         patch("agent_worktrees.git_ops.remove_worktree", return_value=True), \
         patch("agent_worktrees.git_ops.git") as git, \
         patch("agent_worktrees.disposition_history.remove"), \
         patch("agent_worktrees.activity.log_event"), \
         patch("agent_worktrees.__main__._json_output") as json_output:
        git.return_value.returncode = 0
        result = cli.cmd_remove_system(args)

    assert result == 0
    json_output.assert_called_once_with({"removed": "managed-1"})
    assert not (tracking_dir / "managed-1.yaml").exists()


def test_remove_system_help_does_not_advertise_force():
    """--force exists (tested above) but must not appear in --help output --
    a caller should hit the refusal message and resolve the blocking state,
    not discover this escape hatch by reading --help."""
    parser = cli.build_parser()
    remove_system_parser = None
    for action in parser._subparsers._group_actions:
        for choice_name, subparser in action.choices.items():
            if choice_name == "remove-system":
                remove_system_parser = subparser
    assert remove_system_parser is not None
    help_text = remove_system_parser.format_help()
    assert "--force" not in help_text


def test_remove_system_refuses_when_checkout_gone_but_branch_unpushed(tmp_path):
    """A missing checkout directory must not bypass the unpushed-commit
    check -- the branch ref (and any commits on it) lives in the shared
    anchor repo regardless of whether the working directory still exists."""
    record, tracking_dir = _record(tmp_path)
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    import subprocess

    def _git(*args, cwd=anchor):
        subprocess.run(
            ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
        )

    _git("init", "--quiet", "-b", "master")
    _git("config", "user.email", "test@example.com")
    _git("config", "user.name", "Test")
    (anchor / "f.txt").write_text("base", encoding="utf-8")
    _git("add", "f.txt")
    _git("commit", "--quiet", "-m", "base")
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=anchor, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    # Simulate having fetched origin/master at `base_sha` -- no real remote
    # needed, just the remote-tracking ref classify/rev-list reads.
    _git("update-ref", "refs/remotes/origin/master", base_sha)
    _git("checkout", "--quiet", "-b", record.branch)
    (anchor / "f.txt").write_text("unpushed change", encoding="utf-8")
    _git("commit", "--quiet", "-am", "unpushed work")
    _git("checkout", "--quiet", "master")
    # Simulate the checkout directory being gone entirely (e.g. /tmp cleared).
    import shutil

    shutil.rmtree(record.worktree_path)
    args = argparse.Namespace(worktree_id=record.worktree_id, json=True, force=False)

    config = types.SimpleNamespace(
        default_repo=types.SimpleNamespace(
            anchor=str(anchor), remote="origin", default_branch="master",
            worktree_root=str(tmp_path / "worktree_root"),
        )
    )

    with patch("agent_worktrees.config.load_config", return_value=config), \
         patch("agent_worktrees.config.tracking_dir", return_value=tracking_dir), \
         patch("agent_worktrees.__main__._json_error") as json_error:
        json_error.return_value = 1
        result = cli.cmd_remove_system(args)

    assert result == 1
    message = json_error.call_args.args[0]
    assert "not merged into" in message
    assert "checkout directory is missing" in message
    assert (tracking_dir / "managed-1.yaml").exists()


def test_remove_system_treats_unknown_classification_as_blocker(tmp_path):
    """A classify_worktree timeout reports UNKNOWN with dirty=0 -- must not
    be read as 'clean' and fall through to forced removal."""
    record, tracking_dir = _record(tmp_path)
    args = argparse.Namespace(worktree_id=record.worktree_id, json=True, force=False)

    unknown_info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.UNKNOWN)
    with patch("agent_worktrees.config.load_config", return_value=_config(tmp_path)), \
         patch("agent_worktrees.config.tracking_dir", return_value=tracking_dir), \
         patch("agent_worktrees.git_ops.classify_worktree", return_value=unknown_info), \
         patch("agent_worktrees.git_ops.is_branch_merged", return_value=True), \
         patch("agent_worktrees.__main__._json_error") as json_error:
        json_error.return_value = 1
        result = cli.cmd_remove_system(args)

    assert result == 1
    message = json_error.call_args.args[0]
    assert "could not classify" in message
    assert (tracking_dir / "managed-1.yaml").exists()


def test_remove_system_refuses_creating_pr_by_default(tmp_path):
    """`creating` is as live as `open` -- an in-flight PR is still an
    obligation even before it has finished opening."""
    record, tracking_dir = _record(tmp_path)
    record.prs = [tracking.PRRecord(state="creating")]
    tracking.save_record(record, tracking_dir / "managed-1.yaml")
    args = argparse.Namespace(worktree_id=record.worktree_id, json=True, force=False)

    with patch("agent_worktrees.config.load_config", return_value=_config(tmp_path)), \
         patch("agent_worktrees.config.tracking_dir", return_value=tracking_dir), \
         patch("agent_worktrees.__main__._json_error") as json_error:
        json_error.return_value = 1
        result = cli.cmd_remove_system(args)

    assert result == 1
    assert "in-progress or open PR" in json_error.call_args.args[0]


def test_remove_system_allows_squash_merged_branch(tmp_path):
    """A worktree branch whose content was squash-merged upstream keeps its
    original (pre-squash) commits locally -- those must not read as
    'unpushed' forever after. `is_branch_merged` (tree/patch-id aware, not
    raw SHA ancestry) is the correct check."""
    record, tracking_dir = _record(tmp_path)
    args = argparse.Namespace(worktree_id=record.worktree_id, json=True, force=False)

    with patch("agent_worktrees.config.load_config", return_value=_config(tmp_path)), \
         patch("agent_worktrees.config.tracking_dir", return_value=tracking_dir), \
         patch("agent_worktrees.sessions.kill_tmux_session"), \
         patch("agent_worktrees.git_ops.is_branch_merged", return_value=True), \
         patch("agent_worktrees.git_ops.list_worktree_paths"), \
         patch("agent_worktrees.git_ops.remove_worktree", return_value=True), \
         patch("agent_worktrees.git_ops.git") as git, \
         patch("agent_worktrees.disposition_history.remove"), \
         patch("agent_worktrees.activity.log_event"), \
         patch("agent_worktrees.__main__._json_output") as json_output:
        git.return_value.returncode = 0
        git.return_value.stdout = "0"
        result = cli.cmd_remove_system(args)

    assert result == 0
    json_output.assert_called_once_with({"removed": "managed-1"})


def test_remove_system_rechecks_immediately_before_removal(tmp_path):
    """The initial check and the removal are not one atomic step -- a claim
    that appears in between (a concurrent writer) must still block removal,
    not just the very first check."""
    record, tracking_dir = _record(tmp_path)
    args = argparse.Namespace(worktree_id=record.worktree_id, json=True, force=False)

    clean_calls = {"n": 0}

    def _blockers(rec, repo):
        clean_calls["n"] += 1
        if clean_calls["n"] == 1:
            return []  # initial check: looked clean
        return ["unsettled outbound resource claim(s): codespace:late-claim"]

    with patch("agent_worktrees.config.load_config", return_value=_config(tmp_path)), \
         patch("agent_worktrees.config.tracking_dir", return_value=tracking_dir), \
         patch("agent_worktrees.__main__._remove_system_blockers", side_effect=_blockers), \
         patch("agent_worktrees.__main__._json_error") as json_error:
        json_error.return_value = 1
        result = cli.cmd_remove_system(args)

    assert result == 1
    assert clean_calls["n"] == 2
    message = json_error.call_args.args[0]
    assert "appeared after the initial check" in message
    assert "late-claim" in message
    assert (tracking_dir / "managed-1.yaml").exists()


def test_remove_system_force_skips_the_immediate_recheck(tmp_path):
    """--force means the caller already accepted the risk -- it must skip
    both the initial check AND the immediate pre-removal recheck."""
    record, tracking_dir = _record(tmp_path)
    args = argparse.Namespace(worktree_id=record.worktree_id, json=True, force=True)

    with patch("agent_worktrees.config.load_config", return_value=_config(tmp_path)), \
         patch("agent_worktrees.config.tracking_dir", return_value=tracking_dir), \
         patch("agent_worktrees.sessions.kill_tmux_session"), \
         patch("agent_worktrees.git_ops.list_worktree_paths"), \
         patch("agent_worktrees.git_ops.remove_worktree", return_value=True), \
         patch("agent_worktrees.git_ops.git") as git, \
         patch("agent_worktrees.disposition_history.remove"), \
         patch("agent_worktrees.activity.log_event"), \
         patch("agent_worktrees.__main__._remove_system_blockers") as blockers, \
         patch("agent_worktrees.__main__._json_output") as json_output:
        git.return_value.returncode = 0
        git.return_value.stdout = "0"
        result = cli.cmd_remove_system(args)

    assert result == 0
    blockers.assert_not_called()
    json_output.assert_called_once_with({"removed": "managed-1"})
