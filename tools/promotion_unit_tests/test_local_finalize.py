"""Actor-free local-finality decisions; Git and checkout observations are boundaries."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


SOURCE = Path(__file__).resolve().parents[2] / "plugins/agent-worktrees/src/agent_worktrees/finalize_local.py"


@pytest.fixture(scope="session")
def source_code():
    return compile(SOURCE.read_bytes(), str(SOURCE), "exec")


@pytest.fixture
def api(monkeypatch, source_code):
    package = ModuleType("promotion_local_finalize")
    package.__path__ = []
    git_ops = ModuleType(package.__name__ + ".git_ops")
    package.git_ops = git_ops
    monkeypatch.setitem(sys.modules, package.__name__, package)
    monkeypatch.setitem(sys.modules, git_ops.__name__, git_ops)
    spec = importlib.util.spec_from_file_location(package.__name__ + ".implementation", SOURCE)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    exec(source_code, module.__dict__)
    monkeypatch.setattr(module, "Path", lambda path: SimpleNamespace(exists=lambda: True))
    repo = SimpleNamespace(anchor="anchor", remote="origin", default_branch="main",
                           pr=SimpleNamespace(enabled=False, required=False))
    return module, git_ops, repo


@pytest.mark.parametrize("state", ["local", "remote", "unknown", "pr-required"])
def test_upstream_requires_positive_remote_free_observation(api, state):
    module, git_ops, repo = api
    git_ops.git = lambda *a, **k: SimpleNamespace(
        returncode=1 if state == "unknown" else 0,
        stdout="origin\n" if state == "remote" else "",
    )
    repo.pr.required = state == "pr-required"
    if state in ("unknown", "pr-required"):
        with pytest.raises(ValueError):
            module.resolve_upstream(repo, pr_mode=False)
    else:
        expected = ("origin/main", False) if state == "remote" else ("refs/heads/main", True)
        assert module.resolve_upstream(repo, pr_mode=False) == expected


@pytest.mark.parametrize("state", ["contained", "new-commit", "unknown-ref", "dirty", "remote-added"])
def test_local_completion_requires_fresh_clean_containment(api, state):
    module, git_ops, repo = api
    commits = ("a" * 40, "b" * 40)
    calls = []

    def git(*args, **kwargs):
        calls.append(args)
        if args == ("remote",):
            return SimpleNamespace(returncode=0, stdout="origin\n" if state == "remote-added" else "")
        if args[0] == "rev-parse":
            return SimpleNamespace(
                returncode=0, stdout=commits[0] if state == "unknown-ref" else "\n".join(commits),
            )
        assert args == ("merge-base", "--is-ancestor", *commits)
        return SimpleNamespace(returncode=1 if state == "new-commit" else 0, stdout="")

    git_ops.git = git
    git_ops.is_clean = lambda **kwargs: state != "dirty"
    error = module.content_error(repo, "checkout", "worktree/task", "refs/heads/main")
    assert (error is None) is (state == "contained")
    assert not any(args[0] in ("fetch", "merge", "reset", "rebase") for args in calls)
