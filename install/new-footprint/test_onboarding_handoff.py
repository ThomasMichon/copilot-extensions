"""Cold capability checks must use the incoming executor, not the bootstrap kit."""
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

import onboard


def test_fresh_clone_resolves_remote_branch_and_refuses_ambiguous_tag(tmp_path: Path) -> None:
    from worktree_manager.bootstrap_harness import _git

    source, clone = tmp_path / "source", tmp_path / "clone"
    source.mkdir()
    _git(source, "init", "-q", "-b", "main")
    _git(source, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
         "commit", "-q", "--allow-empty", "-m", "baseline")
    _git(source, "checkout", "-qb", "topic")
    _git(source, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
         "commit", "-q", "--allow-empty", "-m", "topic")
    tip = _git(source, "rev-parse", "HEAD")
    _git(source, "checkout", "-q", "main")
    _git(tmp_path, "clone", "-q", "--no-checkout", str(source), str(clone))
    assert onboard.resolve_commit(clone, "topic") == tip
    _git(clone, "tag", "topic", "HEAD")
    with pytest.raises(ValueError, match="Ambiguous"):
        onboard.resolve_commit(clone, "topic")
    assert onboard.resolve_commit(clone, "refs/heads/topic") == tip
    assert onboard.resolve_commit(clone, "refs/tags/topic") == _git(source, "rev-parse", "HEAD")


def test_select_each_harness_independently(tmp_path: Path) -> None:
    anchors = {name: tmp_path / name for name in ("default", "other", "bound")}
    choices = [("explicit", "example/one"), ("portable", "example/two"), ("local", "retained")]
    with patch("worktree_manager.bootstrap_harness._git", return_value=""), patch(
        "worktree_manager.bootstrap_pairing.select", side_effect=choices,
    ) as select:
        result = onboard.selected_companions(tmp_path, "default", anchors, "example/one", tmp_path)
    assert result == {"default": "example/one", "other": "example/two"}
    assert [(call.args[0].parent.name, call.args[2]) for call in select.call_args_list] == [
        ("default", "example/one"), ("other", None), ("bound", None),
    ]


@pytest.mark.parametrize("exit_code", [0, 1])
def test_probe_uses_incoming_environment(tmp_path: Path, exit_code: int) -> None:
    python = tmp_path / "executor" / "python"
    env = {"UV_OFFLINE": "1"}
    with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], exit_code)) as run:
        if exit_code:
            with pytest.raises(RuntimeError, match="no app slots staged"):
                onboard.require_onboarding(python, env)
        else:
            onboard.require_onboarding(python, env)
        assert run.call_args.args[0][:3] == [str(python), "-I", "-B"]
        assert run.call_args.kwargs["env"] == env
        assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("remote", [
    "https://github.com/example/knowledge",
    "https://dev.azure.com/example/project/_git/knowledge",
])
def test_selected_repository_needs_no_visibility_api(tmp_path: Path, remote: str) -> None:
    (tmp_path / "registry").mkdir()
    argv = ["onboard", "--root", str(tmp_path), "--workspace", str(tmp_path),
            "--srcroot", str(tmp_path), "--knowledge-repo", remote]
    with patch("sys.argv", argv), \
            patch("worktree_manager.bootstrap_harness.refresh_registrations", return_value=tmp_path), \
            patch("worktree_manager.registered_suite.registrations", return_value=("h", {"h": tmp_path})), \
            patch.object(onboard, "selected_companions", return_value={"h": remote}), \
            patch.object(subprocess, "run", side_effect=AssertionError("unexpected provider lookup")), \
            patch("worktree_manager.runtime_source_repository.read", side_effect=LookupError("source selection")), \
            pytest.raises(LookupError, match="source selection"):
        onboard.main()
