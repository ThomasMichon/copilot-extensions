"""Actor-free local-finality decisions; Git and checkout observations are boundaries."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


SOURCE = Path(__file__).resolve().parents[2] / "plugins/agent-worktrees/src/agent_worktrees/finalize_local.py"


@pytest.fixture(scope="session")
def source_code():
    return {
        name: compile(path.read_bytes(), str(path), "exec")
        for name, path in (
            ("implementation", SOURCE),
            ("git_repository_environment", SOURCE.with_name("git_repository_environment.py")),
        )
    }


@pytest.fixture
def api(monkeypatch, source_code):
    package = ModuleType("promotion_local_finalize")
    package.__path__ = []
    git_ops = ModuleType(package.__name__ + ".git_ops")
    package.git_ops = git_ops
    monkeypatch.setitem(sys.modules, package.__name__, package)
    monkeypatch.setitem(sys.modules, git_ops.__name__, git_ops)
    env_scrub = ModuleType(package.__name__ + ".env_scrub")
    monkeypatch.setitem(sys.modules, env_scrub.__name__, env_scrub)
    package.env_scrub = env_scrub
    identity_spec = importlib.util.spec_from_file_location(
        package.__name__ + ".git_repository_environment",
        SOURCE.with_name("git_repository_environment.py"),
    )
    identity = importlib.util.module_from_spec(identity_spec)
    monkeypatch.setitem(sys.modules, identity_spec.name, identity)
    exec(source_code["git_repository_environment"], identity.__dict__)
    git_ops.identity_scoped = identity.identity_scoped
    spec = importlib.util.spec_from_file_location(package.__name__ + ".implementation", SOURCE)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    exec(source_code["implementation"], module.__dict__)
    monkeypatch.setattr(module, "Path", lambda path: SimpleNamespace(exists=lambda: True))
    repo = SimpleNamespace(anchor="anchor", remote="origin", default_branch="main",
                           pr=SimpleNamespace(enabled=False, required=False))
    return module, git_ops, repo


@pytest.mark.parametrize("state", ["local", "remote", "unknown", "pr-required"])
def test_upstream_requires_positive_remote_free_observation(api, state):
    module, git_ops, repo = api

    def git(*args, **kwargs):
        assert git_ops.identity_scoped()
        return SimpleNamespace(
            returncode=1 if state == "unknown" else 0,
            stdout="origin\n" if state == "remote" else "",
        )

    git_ops.git = git
    repo.pr.required = state == "pr-required"
    if state in ("unknown", "pr-required"):
        with pytest.raises(ValueError):
            module.resolve_upstream(repo, pr_mode=False)
    else:
        expected = ("origin/main", False) if state == "remote" else ("refs/heads/main", True)
        assert module.resolve_upstream(repo, pr_mode=False) == expected
    assert not git_ops.identity_scoped()


@pytest.mark.parametrize("state", [
    "contained", "new-commit", "unknown-ref", "dirty", "unknown-status", "remote-added",
])
def test_local_completion_requires_fresh_clean_containment(api, state):
    module, git_ops, repo = api
    commits = ("a" * 40, "b" * 40)
    calls = []

    def git(*args, **kwargs):
        assert git_ops.identity_scoped()
        calls.append(args)
        if args == ("remote",):
            return SimpleNamespace(returncode=0, stdout="origin\n" if state == "remote-added" else "")
        if args[0] == "status":
            assert args == ("status", "--porcelain", "--untracked-files=all", "--ignore-submodules=none")
            return SimpleNamespace(returncode=1 if state == "unknown-status" else 0,
                                   stdout="?? unfinished\n" if state == "dirty" else "")
        if args[0] == "rev-parse":
            default = args[-1] == "refs/heads/main^{commit}"
            assert kwargs["cwd"] == ("anchor" if default else "checkout")
            return SimpleNamespace(
                returncode=0,
                stdout="" if state == "unknown-ref" and default else commits[int(default)],
            )
        assert args == ("merge-base", "--is-ancestor", *commits)
        assert kwargs["cwd"] == "anchor"
        return SimpleNamespace(returncode=1 if state == "new-commit" else 0, stdout="")

    git_ops.git = git
    error = module.content_error(repo, "checkout", "worktree/task", "refs/heads/main")
    assert (error is None) is (state == "contained")
    assert not any(args[0] in ("fetch", "merge", "reset", "rebase") for args in calls)
    assert not git_ops.identity_scoped()
